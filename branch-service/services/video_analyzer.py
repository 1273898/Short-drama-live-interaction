"""
视频分析服务
从短剧视频中提取关键帧和元信息
适配自 test/video_utils.py
"""

import os
import sys
import base64
import subprocess
from typing import Optional

# 尝试导入 cv2，如果不可用则提供降级方案
try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from config import VIDEO_ROOT


def get_video_path(episode_video_path: str) -> str:
    """将相对视频路径转换为绝对路径"""
    full_path = os.path.join(VIDEO_ROOT, episode_video_path)
    if os.path.exists(full_path):
        return full_path
    # 尝试直接使用
    if os.path.exists(episode_video_path):
        return episode_video_path
    return full_path


def get_video_info(video_path: str) -> dict:
    """获取视频基础信息"""
    if not HAS_CV2:
        return _get_video_info_ffprobe(video_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"无法打开视频文件: {video_path}")

    info = {
        "path": video_path,
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "duration_seconds": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)),
    }
    cap.release()
    return info


def _get_video_info_ffprobe(video_path: str) -> dict:
    """使用 ffprobe 获取视频信息（降级方案）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
            capture_output=True, text=True, timeout=30
        )
        import json
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        video_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), {})
        return {
            "path": video_path,
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "fps": eval(video_stream.get("r_frame_rate", "0/1")),
            "total_frames": int(duration * eval(video_stream.get("r_frame_rate", "0/1"))),
            "duration_seconds": int(duration),
        }
    except Exception as e:
        raise ValueError(f"无法获取视频信息: {e}")


def extract_keyframe_at_time(video_path: str, timestamp_seconds: float) -> Optional[bytes]:
    """提取指定时间点的关键帧，返回 JPEG 字节"""
    if not HAS_CV2:
        return _extract_keyframe_ffmpeg(video_path, timestamp_seconds)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_number = int(timestamp_seconds * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

    ret, frame = cap.read()
    cap.release()

    if ret:
        _, buffer = cv2.imencode(".jpg", frame)
        return buffer.tobytes()
    return None


def _extract_keyframe_ffmpeg(video_path: str, timestamp_seconds: float) -> Optional[bytes]:
    """使用 ffmpeg 提取关键帧（降级方案）"""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(timestamp_seconds), "-i", video_path, "-frames:v", "1", "-q:v", "2", tmp_path],
            capture_output=True, timeout=30
        )
        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def extract_end_keyframe(video_path: str, offset_seconds: int = 5) -> Optional[bytes]:
    """提取视频末尾的关键帧（倒数第 offset_seconds 秒）"""
    info = get_video_info(video_path)
    duration = info["duration_seconds"]
    target_time = max(0, duration - offset_seconds)
    return extract_keyframe_at_time(video_path, target_time)


def extract_last_n_seconds_keyframes(
    video_path: str, n_seconds: int = 30, num_frames: int = 2
) -> list:
    """提取视频末尾 N 秒的关键帧"""
    info = get_video_info(video_path)
    duration = info["duration_seconds"]

    start_time = max(0, duration - n_seconds)
    step = (duration - start_time) / num_frames if num_frames > 0 else 0

    keyframes = []
    for i in range(num_frames):
        ts = start_time + i * step
        frame_bytes = extract_keyframe_at_time(video_path, ts)
        if frame_bytes is not None:
            keyframes.append((float(ts), frame_bytes))

    return keyframes


def frame_to_base64(frame_bytes: bytes) -> str:
    """将 JPEG 字节转换为 base64 字符串"""
    return base64.b64encode(frame_bytes).decode("utf-8")
