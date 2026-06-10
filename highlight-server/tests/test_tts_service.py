"""TTS 服务容错测试：重试、超时、熔断器、缓存隔离"""
import asyncio
import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tts_service import TTSService, _TTS_CACHE_VERSION, TTS_CACHE_DIR


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_service() -> TTSService:
    """构造一个 available=True 的 TTSService（跳过真实 edge_tts 导入）"""
    svc = TTSService.__new__(TTSService)
    svc._available = True
    svc._edge_tts = MagicMock()
    svc._consecutive_fails = 0
    svc._circuit_breaker_until = 0.0
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    return svc


def _fake_save(filepath, content=b"\x00" * 200):
    """模拟 edge_tts.Communicate.save：写入有效音频文件"""
    async def _save(path):
        with open(path, "wb") as f:
            f.write(content)
    return _save


def _failing_save(error):
    """模拟 edge_tts.Communicate.save：抛出指定异常"""
    async def _save(path):
        raise error
    return _save


def _make_connector_error(msg="网络不可达"):
    """构造连接异常（兼容有/无 aiohttp 环境）。

    tts_service._NETWORK_ERRORS 在 aiohttp 可用时包含 aiohttp.ClientConnectorError，
    不可用时回退到 (TimeoutError, ConnectionError, OSError)。
    ConnectionError 属于两种情况的交集，始终会被重试逻辑捕获。
    """
    return ConnectionError(msg)


# ── 正常合成 ─────────────────────────────────────────────────────────────────

class TestTTSSynthesizeSuccess:

    def test_synthesize_returns_filename(self):
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("测试文本", "震惊")
            filepath = os.path.join(TTS_CACHE_DIR, filename)
            try:
                comm = MagicMock()
                comm.save = _fake_save(filepath)
                svc._edge_tts.Communicate.return_value = comm

                result = await svc.synthesize("测试文本", "震惊")
                assert result is not None
                assert result.endswith(".mp3")
                assert os.path.exists(os.path.join(TTS_CACHE_DIR, result))
                assert svc._consecutive_fails == 0
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())

    def test_cache_hit_skips_synthesis(self):
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("缓存文本", "愤怒")
            filepath = os.path.join(TTS_CACHE_DIR, filename)
            try:
                with open(filepath, "wb") as f:
                    f.write(b"\x00" * 200)
                result = await svc.synthesize("缓存文本", "愤怒")
                assert result == filename
                svc._edge_tts.Communicate.assert_not_called()
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())


# ── 重试机制 ─────────────────────────────────────────────────────────────────

class TestTTSSynthesizeRetry:

    def test_retry_on_connection_error(self):
        """网络错误应重试 3 次"""
        async def _run():
            svc = _make_service()
            comm = MagicMock()
            comm.save = _failing_save(_make_connector_error("网络不可达"))
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("重试测试", "吃瓜")

            assert result is None
            assert svc._edge_tts.Communicate.call_count == 3
        asyncio.run(_run())

    def test_retry_on_timeout(self):
        """超时应重试 3 次"""
        async def _run():
            svc = _make_service()
            comm = MagicMock()
            comm.save = _failing_save(asyncio.TimeoutError())
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("超时测试", "期待")

            assert result is None
            assert svc._edge_tts.Communicate.call_count == 3
        asyncio.run(_run())

    def test_success_after_retry(self):
        """前两次失败，第三次成功"""
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("间歇成功", "爆笑")
            filepath = os.path.join(TTS_CACHE_DIR, filename)

            call_count = 0

            def make_comm(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                comm = MagicMock()
                if call_count <= 2:
                    comm.save = _failing_save(_make_connector_error("临时故障"))
                else:
                    comm.save = _fake_save(filepath)
                return comm

            svc._edge_tts.Communicate.side_effect = make_comm

            try:
                with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                    result = await svc.synthesize("间歇成功", "爆笑")
                assert result is not None
                assert call_count == 3
                assert svc._consecutive_fails == 0
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())


# ── 熔断器 ───────────────────────────────────────────────────────────────────

class TestTTSCircuitBreaker:

    def test_circuit_opens_after_threshold(self):
        """连续失败达到阈值后触发熔断"""
        async def _run():
            svc = _make_service()
            comm = MagicMock()
            comm.save = _failing_save(_make_connector_error("持续故障"))
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                # 3 次 synthesize 调用均失败，触发熔断
                for i in range(3):
                    await svc.synthesize(f"熔断测试{i}", "无语")
                assert svc._consecutive_fails == 3
                assert svc._circuit_breaker_until > time.time()

                # 熔断中，不应调用 Communicate
                svc._edge_tts.Communicate.reset_mock()
                result = await svc.synthesize("熔断测试_跳过", "无语")
                assert result is None
                svc._edge_tts.Communicate.assert_not_called()
        asyncio.run(_run())

    def test_circuit_resets_after_cooldown(self):
        """冷却期结束后应恢复尝试"""
        async def _run():
            svc = _make_service()
            # 手动设置熔断状态，但冷却期已过
            svc._consecutive_fails = 3
            svc._circuit_breaker_until = time.time() - 1

            filename = svc._cache_key("冷却恢复", "震惊")
            filepath = os.path.join(TTS_CACHE_DIR, filename)
            comm = MagicMock()
            comm.save = _fake_save(filepath)
            svc._edge_tts.Communicate.return_value = comm

            try:
                result = await svc.synthesize("冷却恢复", "震惊")
                assert result is not None
                assert svc._consecutive_fails == 0
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())

    def test_success_resets_circuit_counter(self):
        """成功合成应重置连续失败计数"""
        async def _run():
            svc = _make_service()
            svc._consecutive_fails = 2

            filename = svc._cache_key("重置计数", "感动")
            filepath = os.path.join(TTS_CACHE_DIR, filename)
            comm = MagicMock()
            comm.save = _fake_save(filepath)
            svc._edge_tts.Communicate.return_value = comm

            try:
                result = await svc.synthesize("重置计数", "感动")
                assert result is not None
                assert svc._consecutive_fails == 0
                assert svc._circuit_breaker_until == 0.0
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())


# ── 缓存隔离 ─────────────────────────────────────────────────────────────────

class TestTTSCacheIsolation:

    def test_failure_not_cached(self):
        """失败不应产生缓存文件"""
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("失败不缓存", "嘲笑")
            filepath = os.path.join(TTS_CACHE_DIR, filename)

            comm = MagicMock()
            comm.save = _failing_save(_make_connector_error("连接失败"))
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("失败不缓存", "嘲笑")

            assert result is None
            assert not os.path.exists(filepath), "失败的 TTS 不应留下缓存文件"
        asyncio.run(_run())

    def test_empty_output_not_cached(self):
        """空输出文件应被清理"""
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("空输出", "心疼")
            filepath = os.path.join(TTS_CACHE_DIR, filename)

            async def empty_save(path):
                with open(path, "wb") as f:
                    f.write(b"\x00")

            comm = MagicMock()
            comm.save = empty_save
            svc._edge_tts.Communicate.return_value = comm

            result = await svc.synthesize("空输出", "心疼")
            assert result is None
            assert not os.path.exists(filepath), "空输出文件应被清理"
        asyncio.run(_run())

    def test_small_file_threshold(self):
        """100 bytes 的文件不应被接受（需要 > 100）"""
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("边界测试", "期待")
            filepath = os.path.join(TTS_CACHE_DIR, filename)

            async def exact_100_save(path):
                with open(path, "wb") as f:
                    f.write(b"\x00" * 100)

            comm = MagicMock()
            comm.save = exact_100_save
            svc._edge_tts.Communicate.return_value = comm

            result = await svc.synthesize("边界测试", "期待")
            assert result is None
        asyncio.run(_run())


# ── 网络异常 ─────────────────────────────────────────────────────────────────

class TestTTSNetworkErrors:

    def test_ssl_error(self):
        """SSL 错误应被捕获并重试"""
        async def _run():
            svc = _make_service()
            ssl_err = ConnectionError("[SSL: WRONG_VERSION_NUMBER] wrong version number")
            comm = MagicMock()
            comm.save = _failing_save(ssl_err)
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("SSL测试", "愤怒")

            assert result is None
            assert svc._edge_tts.Communicate.call_count == 3
        asyncio.run(_run())

    def test_dns_error(self):
        """DNS 解析失败应被捕获"""
        async def _run():
            svc = _make_service()
            comm = MagicMock()
            comm.save = _failing_save(OSError("[Errno 11001] getaddrinfo failed"))
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("DNS测试", "爆笑")

            assert result is None
            assert svc._edge_tts.Communicate.call_count == 3
        asyncio.run(_run())

    def test_server_disconnect(self):
        """服务端断开连接应被捕获"""
        async def _run():
            svc = _make_service()
            comm = MagicMock()
            comm.save = _failing_save(ConnectionError("服务端断开连接"))
            svc._edge_tts.Communicate.return_value = comm

            with patch("tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize("断连测试", "无语")

            assert result is None
            assert svc._edge_tts.Communicate.call_count == 3
        asyncio.run(_run())


# ── 边界场景 ─────────────────────────────────────────────────────────────────

class TestTTSEdgeCases:

    def test_empty_text_returns_none(self):
        svc = _make_service()
        result = asyncio.run(svc.synthesize("", "震惊"))
        assert result is None

    def test_blank_text_returns_none(self):
        svc = _make_service()
        result = asyncio.run(svc.synthesize("   ", "愤怒"))
        assert result is None

    def test_unavailable_service_returns_none(self):
        svc = _make_service()
        svc._available = False
        result = asyncio.run(svc.synthesize("不可用", "吃瓜"))
        assert result is None

    def test_unknown_emotion_uses_default_voice(self):
        async def _run():
            svc = _make_service()
            filename = svc._cache_key("未知情绪", "未知标签")
            filepath = os.path.join(TTS_CACHE_DIR, filename)
            comm = MagicMock()
            comm.save = _fake_save(filepath)
            svc._edge_tts.Communicate.return_value = comm

            try:
                result = await svc.synthesize("未知情绪", "未知标签")
                assert result is not None
                call_kwargs = svc._edge_tts.Communicate.call_args
                assert call_kwargs[1]["voice"] == "zh-CN-XiaoxiaoNeural"
            finally:
                if os.path.exists(filepath):
                    os.unlink(filepath)
        asyncio.run(_run())


# ── _cleanup_file ────────────────────────────────────────────────────────────

class TestTTSCleanupFile:

    def test_cleanup_existing_file(self):
        filepath = os.path.join(TTS_CACHE_DIR, "_test_cleanup.mp3")
        with open(filepath, "wb") as f:
            f.write(b"\x00")
        assert os.path.exists(filepath)
        TTSService._cleanup_file(filepath)
        assert not os.path.exists(filepath)

    def test_cleanup_nonexistent_file(self):
        TTSService._cleanup_file("/nonexistent/path/file.mp3")