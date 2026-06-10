"""FastAPI 接口：高光点识别服务（带缓存和降级）

服务端口：3000（与 Android 客户端 HighlightApiClient.kt 中 BASE_URL 一致）
"""

import os
import sys
import time
import asyncio
import logging
import glob
import json
import traceback
from typing import List, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, FileResponse
from pydantic import BaseModel

# 加载 .env 文件（优先级低于系统环境变量）
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import rule_engine
import ai_engine
import fusion_engine
import roast_generator
from cache_manager import CacheManager
from degradation import DegradationManager

logger = logging.getLogger(__name__)

# 全局缓存管理器
cache = CacheManager(cache_base_dir="./cache")


async def _warmup_videos(video_ids: list):
    """后台预热指定视频"""
    for video_id in video_ids:
        try:
            video_path = find_video_path(video_id)
            if video_path and not cache.get_rules(video_id):
                rule_highlights = await rule_engine.analyze_video_async(video_path)
                cache.set_rules(video_id, rule_highlights)
                logger.info(f"Warmed up: {video_id}")
        except Exception as e:
            logger.warning(f"Warmup failed for {video_id}: {e}")


CACHE_VERSION = 4


async def get_video_duration(video_path: str) -> Optional[float]:
    """获取视频时长（秒），失败返回 None。使用异步子进程避免阻塞事件循环。"""
    import re

    # Try ffprobe first, then ffmpeg fallback
    tools = [
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
    ]

    # Fallback: use imageio-ffmpeg's bundled ffmpeg
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        tools.append([ffmpeg_path, "-i", video_path, "-f", "null", "-"])
    except ImportError:
        pass

    for cmd in tools:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            stdout_str = stdout.decode('utf-8', errors='replace')
            stderr_str = stderr.decode('utf-8', errors='replace')
            # ffprobe outputs duration directly
            if proc.returncode == 0 and stdout_str.strip():
                try:
                    return float(stdout_str.strip())
                except ValueError:
                    pass
            # ffmpeg outputs duration in stderr: "Duration: HH:MM:SS.xx"
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr_str)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception as e:
            logger.debug(f"获取视频时长尝试失败 ({cmd[0]}): {video_path}: {e}")

    logger.warning(f"获取视频时长失败: {video_path}")
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动/关闭生命周期"""
    cache.l2_highlights.cleanup_expired()
    cache.l3_speech.cleanup_expired()
    cache.l4_rules.cleanup_expired()

    # 全量清除所有文件缓存（L2/L3/L4），强制按新规则重新生成
    import shutil
    for cache_name, cache_obj in [
        ("L2-高光", cache.l2_highlights),
        ("L3-语音", cache.l3_speech),
        ("L4-规则", cache.l4_rules)
    ]:
        cache_dir = cache_obj.cache_dir
        if os.path.isdir(cache_dir):
            cleared = 0
            for fname in os.listdir(cache_dir):
                if fname.endswith('.json'):
                    try:
                        os.unlink(os.path.join(cache_dir, fname))
                        cleared += 1
                    except OSError:
                        pass
            if cleared > 0:
                logger.info(f"启动清理：清除 {cleared} 个{cache_name}缓存文件")
    # 清除 L1 内存缓存
    cache.l1._cache.clear()
    logger.info("启动清理：L1 内存缓存已清除")

    warmup_ids = os.environ.get("WARMUP_VIDEO_IDS", "")
    if warmup_ids:
        video_ids = [vid.strip() for vid in warmup_ids.split(",") if vid.strip()]
        if video_ids:
            asyncio.create_task(_warmup_videos(video_ids))

    yield


app = FastAPI(title="高光点识别服务", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def client_disconnect_middleware(request: Request, call_next):
    """中间件：捕获客户端断开连接，避免协程泄漏和冗长 Traceback。
    对流式响应（视频文件）也有效：CancelledError 可能在 call_next 内部或响应体迭代期间抛出。"""
    try:
        response = await call_next(request)
        return response
    except asyncio.CancelledError:
        logger.info(f"客户端断开: {request.method} {request.url.path}")
        return JSONResponse(
            status_code=499,
            content={"error": "client_disconnected"},
        )
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        logger.info(f"连接异常(客户端断开): {request.method} {request.url.path}: {e}")
        return JSONResponse(
            status_code=499,
            content={"error": "client_disconnected"},
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器：捕获所有未处理的异常"""
    if isinstance(exc, asyncio.CancelledError):
        logger.info(f"请求取消(客户端断开): {request.method} {request.url.path}")
        return JSONResponse(status_code=499, content={"error": "client_disconnected"})
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, OSError)):
        logger.info(f"连接异常(客户端断开): {request.method} {request.url.path}: {exc}")
        return JSONResponse(status_code=499, content={"error": "client_disconnected"})
    logger.error(f"未捕获异常: {request.method} {request.url} -> {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
        },
    )


class BatchRequest(BaseModel):
    video_ids: List[str]


# ===== 剧集 API 数据（与 video-server/server.js 保持一致） =====
DRAMAS = [
    {
        "drama_id": 1,
        "title": "北派寻宝笔记",
        "description": "神秘的北派寻宝之旅，探寻失落的宝藏",
        "cover": "/北派寻宝笔记/cover/9472672297f3cdd1324733ff87ba1273~tplv-s85hriknmn-jpeg.jpg"
    },
    {
        "drama_id": 2,
        "title": "天下第一纨绔",
        "description": "纨绔子弟逆袭成长的热血故事",
        "cover": "/天下第一纨绔/cover/689333212bd7d566acd343d404d4b454~tplv-s85hriknmn-jpeg.jpg"
    }
]


def get_episodes(drama_id: int):
    if drama_id == 1:
        return [
            {"episode_id": i + 1, "title": f"第{63 + i}集", "episode_number": 63 + i,
             "video_url": f"/北派寻宝笔记/第{63 + i}集.mp4"}
            for i in range(10)
        ]
    if drama_id == 2:
        return [
            {"episode_id": 101 + i, "title": f"第{i + 1}集", "episode_number": i + 1,
             "video_url": f"/天下第一纨绔/第{i + 1}集.mp4"}
            for i in range(10)
        ]
    return []


def get_episode_context(video_id: str) -> tuple:
    """根据 video_id 查找剧名和集名，用于吐槽生成的上下文"""
    for drama in DRAMAS:
        episodes = get_episodes(drama["drama_id"])
        for ep in episodes:
            ep_video_id = ep["video_url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if ep_video_id == video_id:
                return drama["title"], ep["title"]
    # 从 video_id 推断（如 "第63集" -> "北派寻宝笔记"）
    for drama in DRAMAS:
        for ep in get_episodes(drama["drama_id"]):
            if video_id in ep["video_url"]:
                return drama["title"], ep["title"]
    return "未知剧集", video_id


@app.get("/api/dramas")
async def api_dramas():
    logger.info("API请求: GET /api/dramas")
    return {"dramas": DRAMAS}


@app.get("/api/dramas/{drama_id}/episodes")
async def api_episodes(drama_id: int):
    logger.info(f"API请求: GET /api/dramas/{drama_id}/episodes")
    return {"episodes": get_episodes(drama_id)}


_VIDEO_EXTENSIONS = (".mp4", ".avi", ".mkv", ".mov")


def find_video_path(video_id: str) -> Optional[str]:
    """扫描 短剧/ 目录查找匹配的视频文件，支持多种扩展名"""
    base_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "短剧")
    if not os.path.isdir(base_dir):
        logger.warning(f"短剧目录不存在: {base_dir}")
        return None

    for ext in _VIDEO_EXTENSIONS:
        pattern = os.path.join(base_dir, "**", f"{video_id}{ext}")
        matches = glob.glob(pattern, recursive=True)
        if matches:
            logger.info(f"找到视频: {video_id} -> {matches[0]}")
            return matches[0]

    logger.warning(f"未找到视频: {video_id} (搜索目录: {base_dir}, 扩展名: {_VIDEO_EXTENSIONS})")
    return None


def find_subtitle_path(video_path: str) -> Optional[str]:
    """查找对应的字幕文件（支持 .srt / .ass / .vtt）"""
    base = video_path.rsplit('.', 1)[0]
    for ext in ('.srt', '.ass', '.vtt'):
        path = base + ext
        if os.path.exists(path):
            logger.info(f"找到字幕: {path}")
            return path
    return None


@app.get("/api/highlights/{video_id}")
async def get_highlights(video_id: str):
    """获取单个视频的高光点"""
    start_time = time.time()
    try:
        video_path = find_video_path(video_id)
        if not video_path:
            raise HTTPException(status_code=404, detail=f"视频 {video_id} 不存在")

        # ===== 缓存查找 =====
        cached_result = cache.get_highlight(video_id)
        if cached_result is not None:
            # 旧缓存没有 comments 字段或版本不匹配，需要重新生成
            needs_refresh = False
            if cached_result.get("cache_version") != CACHE_VERSION:
                needs_refresh = True
                logger.info(f"缓存版本不匹配({cached_result.get('cache_version')} != {CACHE_VERSION})，清除: {video_id}")
            elif cached_result.get("highlights") and any(
                "comments" not in h for h in cached_result["highlights"]
            ):
                needs_refresh = True
                logger.info(f"旧缓存缺少 comments，清除重新生成: {video_id}")

            if needs_refresh:
                cache.invalidate(video_id)
            else:
                cached_result["cached"] = True
                cache.record_response_time((time.time() - start_time) * 1000)
                return cached_result

        # ===== 规则引擎（带超时降级） =====
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        timeout = 180 if file_size_mb > 50 else 120
        logger.info(f"开始分析 {video_id} ({file_size_mb:.1f}MB, 超时={timeout}s)")

        rule_highlights, rule_deg_level = await DegradationManager.execute_with_timeout(
            rule_engine.analyze_video_async(video_path),
            timeout_seconds=timeout,
            fallback_value=[],
            operation_name=f"rule_engine({video_id})"
        )

        if rule_deg_level > 0:
            cache.record_degradation(rule_deg_level)

        if rule_highlights:
            cache.set_rules(video_id, rule_highlights)

        if not rule_highlights and rule_deg_level >= 2:
            default_highlights = DegradationManager.get_default_highlights()
            response = {
                "video_id": video_id,
                "highlights": default_highlights,
                "cached": False,
                "ai_source": "none",
                "degraded": True,
                "degradation_reason": "rule_engine_timeout"
            }
            cache.record_response_time((time.time() - start_time) * 1000)
            return response

        # ===== AI 引擎 =====
        ai_highlights = []
        ai_source = "none"
        degraded = False
        degradation_reason = None
        subtitles = []

        try:
            srt_path = find_subtitle_path(video_path)
            if srt_path:
                logger.info(f"使用字幕文件分析: {video_id}")
                subtitles = ai_engine.parse_srt(srt_path)
                segments = ai_engine.segment_subtitles(subtitles)
                ai_highlights = ai_engine.analyze_with_claude(segments, video_id)
                ai_source = "subtitle"
                if not ai_highlights:
                    logger.warning(f"字幕分析返回空结果: {video_id}")
                    degraded = True
                    degradation_reason = "ai_empty_result"
                    cache.record_degradation(1)
            else:
                stt_mode = os.environ.get("STT_MODE", "disabled").lower()
                if stt_mode == "vosk":
                    logger.info(f"使用语音识别分析: {video_id}")
                    cached_speech = cache.get_speech(video_id)

                    if cached_speech is not None:
                        windowed = ai_engine.segment_subtitles(cached_speech)
                        if windowed:
                            ai_highlights = ai_engine._analyze_with_claude_speech(windowed, video_id)
                        ai_source = "speech_recognition"
                    else:
                        ai_highlights = ai_engine.analyze_video_with_speech(video_path, video_id)
                        if ai_highlights:
                            ai_source = "speech_recognition"
                        else:
                            degraded = True
                            degradation_reason = "speech_recognition_failed"
                            cache.record_degradation(1)
                else:
                    logger.info(f"STT 未启用 (mode={stt_mode})，仅使用规则引擎: {video_id}")
                    degraded = True
                    degradation_reason = "stt_disabled"
                    cache.record_degradation(1)
        except Exception as e:
            logger.error(f"AI engine exception for {video_id}: {e}\n{traceback.format_exc()}")
            degraded = True
            degradation_reason = f"ai_exception: {str(e)[:100]}"
            cache.record_degradation(1)

        # ===== 获取视频时长（用于间隔过滤） =====
        video_duration = get_video_duration(video_path)
        if video_duration:
            logger.info(f"视频时长: {video_duration:.1f}s")

        # ===== 融合 =====
        final_highlights = fusion_engine.fuse_highlights(
            rule_highlights, ai_highlights, video_duration=video_duration
        )

        # ===== 为每个高光点附加附近字幕上下文 =====
        if final_highlights and subtitles:
            for hl in final_highlights:
                ts = hl.get("timestamp", 0)
                # 提取高光点前后 15 秒的字幕文本
                nearby = [
                    s["text"] for s in subtitles
                    if s["start_time"] >= ts - 15 and s["start_time"] <= ts + 15
                ]
                if nearby:
                    hl["subtitle_context"] = " ".join(nearby)

        # ===== 预生成吐槽评论 =====
        if final_highlights:
            drama_title, episode_title = get_episode_context(video_id)
            logger.info(f"为 {len(final_highlights)} 个高光点生成吐槽: {drama_title} - {episode_title}")
            try:
                final_highlights = await asyncio.to_thread(
                    roast_generator.generate_comments_for_highlights,
                    final_highlights,
                    drama_title=drama_title,
                    episode_title=episode_title
                )
            except Exception as e:
                logger.error(f"吐槽生成异常: {e}")
                for hl in final_highlights:
                    if "comments" not in hl:
                        hl["comments"] = []

        response = {
            "video_id": video_id,
            "highlights": final_highlights,
            "cached": False,
            "ai_source": ai_source,
            "cache_version": CACHE_VERSION,
        }

        if degraded:
            response["degraded"] = True
            response["degradation_reason"] = degradation_reason

        cache.set_highlight(video_id, response)
        cache.record_response_time((time.time() - start_time) * 1000)
        return response
    except asyncio.CancelledError:
        logger.warning(f"请求被取消: {video_id}，客户端可能已断开")
        raise


@app.post("/api/highlights/batch")
async def get_batch_highlights(request: BatchRequest):
    """批量处理，缓存命中的直接返回，未命中的2并行处理，单个失败不影响整体"""
    start_time = time.time()
    batch_timeout = 200  # 单个视频超时 200 秒
    results = {}
    uncached_ids = []

    for video_id in request.video_ids:
        try:
            cached = cache.get_highlight(video_id)
            if cached is not None:
                cached["cached"] = True
                results[video_id] = {"status": "ok", "data": cached}
            else:
                uncached_ids.append(video_id)
        except Exception as e:
            logger.error(f"批量请求缓存查找失败 {video_id}: {e}")
            results[video_id] = {"status": "error", "error": str(e)}

    if uncached_ids:
        # 2并行处理，限制并发数避免资源竞争
        semaphore = asyncio.Semaphore(2)
        completed_count = 0

        async def _safe_get_highlights(vid: str):
            async with semaphore:
                try:
                    return vid, await asyncio.wait_for(get_highlights(vid), timeout=batch_timeout)
                except asyncio.CancelledError:
                    logger.warning(f"批量请求被取消: {vid}")
                    return vid, {"error": "cancelled", "degraded": True}
                except asyncio.TimeoutError:
                    logger.warning(f"批量请求超时: {vid}")
                    return vid, {"error": "timeout", "degraded": True}
                except HTTPException as e:
                    return vid, {"error": e.detail, "status_code": e.status_code}
                except Exception as e:
                    logger.error(f"批量请求处理异常 {vid}: {e}\n{traceback.format_exc()}")
                    return vid, {"error": str(e)}

        tasks = [_safe_get_highlights(vid) for vid in uncached_ids]

        for coro in asyncio.as_completed(tasks):
            vid, resp = await coro
            if isinstance(resp, dict) and "error" in resp and "highlights" not in resp:
                results[vid] = {"status": "error", **resp}
            else:
                results[vid] = {"status": "ok", "data": resp}
            completed_count += 1
            logger.info(f"批量进度: {vid} 完成 ({completed_count}/{len(uncached_ids)})")

    elapsed = time.time() - start_time
    success_count = sum(1 for v in results.values() if v.get("status") == "ok")
    logger.info(f"批量请求完成: {len(request.video_ids)} 个视频, {success_count} 成功, 耗时 {elapsed:.1f}s")

    return {
        "results": results,
        "total": len(request.video_ids),
        "success": success_count,
        "elapsed_ms": round(elapsed * 1000),
    }


@app.get("/api/cache/stats")
async def cache_stats():
    """缓存统计面板"""
    return cache.get_stats()


@app.post("/api/cache/warmup")
async def cache_warmup(request: BatchRequest):
    """缓存预热：后台执行分析"""
    async def _warmup():
        for video_id in request.video_ids:
            try:
                video_path = find_video_path(video_id)
                if video_path:
                    rule_highlights = await rule_engine.analyze_video_async(video_path)
                    cache.set_rules(video_id, rule_highlights)
            except Exception as e:
                logger.warning(f"Warmup failed for {video_id}: {e}")

    asyncio.create_task(_warmup())
    return {"status": "warming", "count": len(request.video_ids)}


@app.post("/api/cache/invalidate/{video_id}")
async def invalidate_cache(video_id: str):
    """清除指定视频的所有缓存"""
    cache.invalidate(video_id)
    return {"status": "invalidated", "video_id": video_id}


@app.post("/api/cache/load")
async def load_cache_from_file():
    """从 batch_result.json 加载预计算数据到缓存，并异步重新生成评论"""
    result_file = os.path.join(os.path.dirname(__file__), "batch_result.json")
    if not os.path.exists(result_file):
        raise HTTPException(status_code=404, detail="batch_result.json 不存在")

    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    loaded = 0
    episodes_to_regen = []
    for video_id, info in data.items():
        if not info.get("highlights"):
            continue
        # 先加载无评论的高光点，让客户端立即可用
        highlights_no_comments = []
        for hl in info["highlights"]:
            hl_clean = {k: v for k, v in hl.items() if k != "comments"}
            highlights_no_comments.append(hl_clean)
        cache_data = {
            "highlights": highlights_no_comments,
            "cached": True,
            "ai_source": info.get("ai_source", "unknown"),
            "cache_version": CACHE_VERSION,
        }
        cache.set_highlight(video_id, cache_data)
        loaded += 1
        episodes_to_regen.append((video_id, info["highlights"]))

    # 异步重新生成评论（不阻塞响应）
    async def regen_comments_background():
        logger.info(f"后台评论生成启动: {len(episodes_to_regen)} 集")
        for idx, (video_id, highlights) in enumerate(episodes_to_regen):
            try:
                drama_title, episode_title = get_episode_context(video_id)
                logger.info(f"后台重新生成评论 [{idx+1}/{len(episodes_to_regen)}]: {video_id} ({drama_title} - {episode_title})")
                # 深拷贝避免修改原始 batch_result.json 加载的数据
                import copy
                highlights_clean = copy.deepcopy(highlights)
                for hl in highlights_clean:
                    hl.pop("comments", None)
                # roast_generator 是同步阻塞的，用 to_thread 包装避免阻塞事件循环
                regenerated = await asyncio.to_thread(
                    roast_generator.generate_comments_for_highlights,
                    highlights_clean, drama_title=drama_title, episode_title=episode_title
                )
                # 更新缓存
                cache_data = {
                    "highlights": regenerated,
                    "cached": True,
                    "ai_source": "speech_recognition",
                    "cache_version": CACHE_VERSION,
                }
                cache.set_highlight(video_id, cache_data)
                comments_count = sum(len(h.get("comments", [])) for h in regenerated)
                logger.info(f"评论重新生成完成 [{idx+1}/{len(episodes_to_regen)}]: {video_id}, {comments_count} 条")
            except Exception as e:
                logger.error(f"评论重新生成失败 [{idx+1}/{len(episodes_to_regen)}]: {video_id}, {e}")

    asyncio.create_task(regen_comments_background())

    return {"status": "ok", "loaded": loaded, "regen_background": True}


@app.post("/api/cache/clear_all")
async def clear_all_cache():
    """清除所有缓存（用于版本升级后强制重新生成）"""
    import shutil
    cleared = []
    for cache_dir in ["cache/highlights", "cache/speech", "cache/rules"]:
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir, exist_ok=True)
            cleared.append(cache_dir)
    # 清除 L1 内存缓存
    cache.l1._cache.clear()
    return {"status": "all_cleared", "directories": cleared}


@app.get("/health")
async def health_check():
    """健康检查"""
    stt_mode = os.environ.get("STT_MODE", "disabled").lower()

    stt_available = False
    vosk_model = None

    if stt_mode == "vosk":
        vosk_model_path = os.environ.get("VOSK_MODEL_PATH", "")
        vosk_model = vosk_model_path
        stt_available = os.path.isdir(vosk_model_path) if vosk_model_path else False

    cache_stats = cache.get_stats()

    return {
        "status": "ok",
        "stt_mode": stt_mode,
        "stt_available": stt_available,
        "ai_engine": "claude",
        "vosk_model": vosk_model,
        "cache": {
            "l1_entries": cache_stats["l1_memory"]["entries"],
            "l2_entries": cache_stats["l2_highlights"]["entries"],
            "l3_entries": cache_stats["l3_speech"]["entries"],
            "l4_entries": cache_stats["l4_rules"]["entries"]
        }
    }


# 挂载视频静态文件目录（与 video-server 功能合并）
VIDEO_ROOT = os.path.join(os.path.dirname(__file__), "..", "短剧")
if os.path.isdir(VIDEO_ROOT):
    from fastapi.responses import FileResponse

    @app.get("/{path:path}")
    async def serve_static(path: str):
        """显式路由：服务 短剧/ 目录下的视频/图片文件"""
        file_path = os.path.join(VIDEO_ROOT, path)
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(file_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=3000,
        timeout_keep_alive=10,       # 缩短 keep-alive 超时，快速清理断开的连接
        limit_max_requests=1000,
        limit_concurrency=50,
        backlog=128,
        log_level="info",
        h11_max_incomplete_event_size=4096,  # 限制未完成事件大小，防止慢速攻击
    )
