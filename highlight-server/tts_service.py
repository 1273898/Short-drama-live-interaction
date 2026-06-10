"""TTS 服务抽象层：Edge-TTS 引擎 + 活泼可爱音色 + 情绪参数调优 + 音频文件缓存

容错策略：
- 3 次重试 + 指数退避（1s/2s/4s）
- 单次请求 10s 超时（asyncio.wait_for）
- 熔断器：连续 3 次失败后冷却 5 分钟，避免无意义重试
- 精细化异常捕获：网络/SSL/超时分类日志
- 失败不写缓存，清理残留空文件
"""

import os
import hashlib
import logging
import time
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 网络相关异常（Edge-TTS 底层 aiohttp + ssl）
try:
    import aiohttp
    _NETWORK_ERRORS = (
        aiohttp.ClientConnectorError,
        aiohttp.ClientOSError,
        aiohttp.ClientResponseError,
        aiohttp.ServerDisconnectedError,
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
    )
except ImportError:
    _NETWORK_ERRORS = (asyncio.TimeoutError, ConnectionError, OSError)

# 缓存版本：音色或参数大改时递增，旧缓存自动失效
_TTS_CACHE_VERSION = "v3"

# 情绪 → Edge-TTS voice + 语速/语调映射
# 主音色：zh-CN-XiaoxiaoNeural（活泼、温暖）
# 辅助音色：zh-CN-XiaoyiNeural（温柔、治愈）用于感动/心疼
# 设计原则：整体 pitch 提升 10~30Hz，rate 提升 5%~20%，打造"灵灵"人设
_EMOTION_VOICE_MAP: Dict[str, Dict[str, Any]] = {
    # 高能情绪：高音调 + 快语速，体现震惊/愤怒的冲击力
    "震惊": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+15%", "pitch": "+30Hz"},
    "愤怒": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+20%", "pitch": "+20Hz"},
    # 爆笑/嘲讽：最高能量，快节奏
    "爆笑": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+20%", "pitch": "+30Hz"},
    "嘲笑": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+15%", "pitch": "+25Hz"},
    # 中性情绪：活泼但不过度
    "吃瓜": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%", "pitch": "+20Hz"},
    "期待": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%", "pitch": "+20Hz"},
    # 温柔情绪：用 XiaoyiNeural，音调稍低但仍保持可爱感
    "感动": {"voice": "zh-CN-XiaoyiNeural", "rate": "+0%", "pitch": "+15Hz"},
    "心疼": {"voice": "zh-CN-XiaoyiNeural", "rate": "-5%", "pitch": "+10Hz"},
    # 低能量：慢语速但仍保持高音调（萝莉音的"无语"感）
    "无语": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%", "pitch": "+15Hz"},
}

_DEFAULT_VOICE = {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%", "pitch": "+20Hz"}

# TTS 缓存目录
TTS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "tts_cache")
# 最大缓存占用（200MB）
MAX_CACHE_BYTES = 200 * 1024 * 1024


class TTSService:
    """Edge-TTS 服务：活泼可爱音色 + 情绪参数调优 + 文件缓存 + LRU 清理"""

    # 熔断器参数
    _CIRCUIT_BREAKER_THRESHOLD = 3   # 连续失败次数触发熔断
    _CIRCUIT_BREAKER_COOLDOWN = 300  # 冷却期 5 分钟

    def __init__(self):
        self._available = False
        self._edge_tts = None
        self._consecutive_fails = 0
        self._circuit_breaker_until = 0.0
        os.makedirs(TTS_CACHE_DIR, exist_ok=True)
        self._init_engine()

    def _init_engine(self):
        """初始化 Edge-TTS 引擎（延迟导入，离线时 graceful 降级）"""
        try:
            import edge_tts
            self._edge_tts = edge_tts
            self._available = True
            logger.info("Edge-TTS 引擎初始化成功（活泼可爱音色模式）")
        except ImportError:
            logger.warning("edge-tts 未安装，TTS 合成不可用（pip install edge-tts）")
        except Exception as e:
            logger.warning(f"Edge-TTS 初始化失败: {e}")

    @property
    def available(self) -> bool:
        return self._available

    def _is_circuit_open(self) -> bool:
        """熔断器是否处于冷却期"""
        if self._consecutive_fails >= self._CIRCUIT_BREAKER_THRESHOLD:
            if time.time() < self._circuit_breaker_until:
                return True
            # 冷却期结束，重置
            self._consecutive_fails = 0
        return False

    def _record_success(self):
        """记录成功，重置熔断器"""
        self._consecutive_fails = 0
        self._circuit_breaker_until = 0.0

    def _record_failure(self):
        """记录失败，达到阈值时触发熔断"""
        self._consecutive_fails += 1
        if self._consecutive_fails >= self._CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_breaker_until = time.time() + self._CIRCUIT_BREAKER_COOLDOWN
            logger.warning(
                f"TTS 熔断器触发：连续 {self._consecutive_fails} 次失败，"
                f"冷却 {self._CIRCUIT_BREAKER_COOLDOWN}s"
            )

    def _cache_key(self, text: str, emotion_tag: str) -> str:
        """基于 text+emotion+voice+version 生成缓存文件名，音色变更自动隔离"""
        params = self._get_voice_params(emotion_tag)
        content = f"{_TTS_CACHE_VERSION}|{params['voice']}|{emotion_tag}|{text}"
        h = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"{h}.mp3"

    def _get_voice_params(self, emotion_tag: str) -> Dict[str, str]:
        """获取情绪对应的音色参数"""
        return _EMOTION_VOICE_MAP.get(emotion_tag, _DEFAULT_VOICE)

    async def synthesize(self, text: str, emotion_tag: str) -> Optional[str]:
        """合成语音，返回音频文件名（相对于 tts_cache/），失败返回 None。

        容错策略：3 次重试 + 指数退避 + 10s 超时 + 熔断器冷却。
        失败不写缓存，清理残留空文件。
        """
        if not self._available or not text.strip():
            return None

        # 熔断器检查：冷却期内直接跳过
        if self._is_circuit_open():
            remaining = int(self._circuit_breaker_until - time.time())
            logger.debug(f"TTS 熔断中，跳过合成（剩余 {remaining}s）: [{emotion_tag}] {text[:20]}...")
            return None

        filename = self._cache_key(text, emotion_tag)
        filepath = os.path.join(TTS_CACHE_DIR, filename)

        # 缓存命中（仅有效音频文件）
        if os.path.exists(filepath) and os.path.getsize(filepath) > 100:
            return filename

        # 3 次重试 + 指数退避
        max_retries = 3
        base_delay = 1.0
        per_attempt_timeout = 10.0

        for attempt in range(max_retries):
            try:
                params = self._get_voice_params(emotion_tag)
                communicate = self._edge_tts.Communicate(
                    text=text,
                    voice=params["voice"],
                    rate=params["rate"],
                    pitch=params["pitch"],
                )
                await asyncio.wait_for(
                    communicate.save(filepath),
                    timeout=per_attempt_timeout,
                )

                # 验证输出文件有效（>100 bytes 避免写入空/损坏文件）
                if os.path.exists(filepath) and os.path.getsize(filepath) > 100:
                    logger.info(
                        f"TTS 合成成功: [{emotion_tag}] {text[:20]}... -> {filename} "
                        f"(voice={params['voice']}, rate={params['rate']}, pitch={params['pitch']})"
                    )
                    self._record_success()
                    return filename
                else:
                    logger.warning(f"TTS 合成输出为空或过小: {filename}")
                    self._cleanup_file(filepath)

            except asyncio.TimeoutError:
                logger.warning(
                    f"TTS 合成超时 ({per_attempt_timeout}s): "
                    f"[{emotion_tag}] {text[:20]}... (尝试 {attempt + 1}/{max_retries})"
                )
                self._cleanup_file(filepath)

            except _NETWORK_ERRORS as e:
                err_type = type(e).__name__
                logger.warning(
                    f"TTS 网络错误 ({err_type}): [{emotion_tag}] {text[:20]}... "
                    f"(尝试 {attempt + 1}/{max_retries}): {e}"
                )
                self._cleanup_file(filepath)

            except Exception as e:
                logger.warning(
                    f"TTS 合成异常: [{emotion_tag}] {text[:20]}... "
                    f"(尝试 {attempt + 1}/{max_retries}): {e}"
                )
                self._cleanup_file(filepath)

            # 指数退避（最后一次不等待）
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        # 全部重试失败
        self._record_failure()
        return None

    @staticmethod
    def _cleanup_file(filepath: str):
        """清理可能残留的空/损坏文件"""
        try:
            if os.path.exists(filepath):
                os.unlink(filepath)
        except OSError:
            pass

    def get_audio_url(self, filename: str) -> str:
        """返回音频的 HTTP URL 路径"""
        return f"/tts/audio/{filename}"

    def cleanup_old_voice_cache(self):
        """清理旧版本缓存文件（v1 格式：纯 hash.mp3，无 voice 前缀）

        新缓存 key 格式：{32-char hex}.mp3（含 voice+version 信息的 SHA256）
        旧缓存 key 格式：{32-char hex}.mp3（仅 text+emotion 的 SHA256）
        无法通过文件名区分，因此在音色大改时直接清空整个 tts_cache/ 目录。
        """
        try:
            count = 0
            for fname in os.listdir(TTS_CACHE_DIR):
                if fname.endswith(".mp3"):
                    fpath = os.path.join(TTS_CACHE_DIR, fname)
                    try:
                        os.unlink(fpath)
                        count += 1
                    except OSError:
                        pass
            if count > 0:
                logger.info(f"TTS 旧缓存清理完成: 删除 {count} 个文件")
        except Exception as e:
            logger.warning(f"TTS 旧缓存清理失败: {e}")

    def cleanup_cache(self):
        """LRU 清理：超过 MAX_CACHE_BYTES 时删除最老的文件"""
        try:
            files = []
            total_size = 0
            for fname in os.listdir(TTS_CACHE_DIR):
                fpath = os.path.join(TTS_CACHE_DIR, fname)
                if os.path.isfile(fpath):
                    size = os.path.getsize(fpath)
                    mtime = os.path.getmtime(fpath)
                    files.append((fpath, size, mtime))
                    total_size += size

            if total_size <= MAX_CACHE_BYTES:
                return

            # 按修改时间排序，删除最老的
            files.sort(key=lambda x: x[2])
            for fpath, size, _ in files:
                if total_size <= MAX_CACHE_BYTES:
                    break
                try:
                    os.unlink(fpath)
                    total_size -= size
                    logger.info(f"TTS 缓存清理: {os.path.basename(fpath)} ({size} bytes)")
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"TTS 缓存清理失败: {e}")

    def cleanup_expired(self, ttl: int = 86400):
        """清理过期文件（与 L2 TTL 一致，默认 24h）"""
        now = time.time()
        try:
            for fname in os.listdir(TTS_CACHE_DIR):
                fpath = os.path.join(TTS_CACHE_DIR, fname)
                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > ttl:
                    try:
                        os.unlink(fpath)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"TTS 过期清理失败: {e}")


# 全局单例
_tts_service: Optional[TTSService] = None


def get_tts_service() -> TTSService:
    """获取 TTS 服务全局单例"""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
