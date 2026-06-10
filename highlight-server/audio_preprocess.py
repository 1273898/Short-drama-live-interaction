"""音频预处理模块：从视频中抽取音频并重采样为标准格式

作为所有音频推理（VAD、ASR、情绪识别）的统一入口。
输出格式：16kHz, 单声道, 16-bit PCM WAV（Silero-VAD 及后续模型的标准输入）。
"""

import io
import os
import re
import sys
import shutil
import subprocess
import tempfile
import logging
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> Optional[str]:
    """查找可用的 ffmpeg 可执行文件路径。

    优先级：
    1. imageio-ffmpeg 捆绑的 ffmpeg（确保版本兼容）
    2. 系统 PATH 中的 ffmpeg
    3. conda 环境 Scripts 目录下的 ffmpeg
    """
    # 优先使用 imageio-ffmpeg
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            logger.debug(f"使用 imageio-ffmpeg: {path}")
            return path
    except ImportError:
        pass

    # 系统 PATH
    path = shutil.which("ffmpeg")
    if path:
        logger.debug(f"使用系统 ffmpeg: {path}")
        return path

    # conda 环境 Scripts 目录
    scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
    candidate = os.path.join(scripts_dir, "ffmpeg.exe")
    if os.path.isfile(candidate):
        logger.debug(f"使用 conda ffmpeg: {candidate}")
        return candidate

    return None


# 模块级缓存 ffmpeg 路径（避免重复查找）
_ffmpeg_path: Optional[str] = None


def get_ffmpeg_path() -> Optional[str]:
    """获取 ffmpeg 路径（懒加载缓存）"""
    global _ffmpeg_path
    if _ffmpeg_path is None:
        _ffmpeg_path = _find_ffmpeg()
        if _ffmpeg_path is None:
            logger.error("ffmpeg 未找到，音频预处理不可用。"
                         "请安装: pip install imageio-ffmpeg 或将 ffmpeg 加入 PATH")
    return _ffmpeg_path


def extract_audio(video_path: str, output_audio_path: str) -> bool:
    """从视频中抽取音频，重采样为 16kHz 单声道 16-bit PCM WAV。

    Args:
        video_path: 输入视频文件路径。
        output_audio_path: 输出 WAV 文件路径（父目录必须存在）。

    Returns:
        True 表示成功，False 表示失败。
    """
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        logger.error("extract_audio 失败: ffmpeg 不可用")
        return False

    if not os.path.isfile(video_path):
        logger.error(f"extract_audio 失败: 视频文件不存在: {video_path}")
        return False

    # 确保输出目录存在
    out_dir = os.path.dirname(output_audio_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # ffmpeg 命令：强制重采样 16kHz, 单声道, 16-bit PCM
    cmd = [
        ffmpeg,
        "-y",                    # 覆盖已有文件
        "-i", video_path,
        "-vn",                   # 不要视频流
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "16000",          # 16kHz 采样率
        "-ac", "1",              # 单声道
        "-f", "wav",             # WAV 容器
        output_audio_path,
    ]

    try:
        logger.info(f"抽取音频: {os.path.basename(video_path)} -> {os.path.basename(output_audio_path)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,  # 5 分钟超时（大视频文件需要更长时间）
        )

        if result.returncode != 0:
            # ffmpeg 错误信息在 stderr
            stderr_tail = result.stderr[-500:] if result.stderr else "(empty)"
            logger.error(f"ffmpeg 抽取失败 (code={result.returncode}): {stderr_tail}")
            # 清理可能的不完整输出文件
            if os.path.exists(output_audio_path):
                try:
                    os.remove(output_audio_path)
                except OSError:
                    pass
            return False

        if not os.path.isfile(output_audio_path):
            logger.error(f"ffmpeg 执行成功但输出文件不存在: {output_audio_path}")
            return False

        file_size = os.path.getsize(output_audio_path)
        logger.info(f"音频抽取成功: {file_size / 1024:.1f} KB")
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg 抽取超时 (>300s): {video_path}")
        return False
    except FileNotFoundError:
        logger.error(f"ffmpeg 可执行文件未找到: {ffmpeg}")
        return False
    except Exception as e:
        logger.error(f"extract_audio 异常: {e}", exc_info=True)
        return False


def get_audio_duration(audio_path: str) -> float:
    """获取音频文件时长（秒）。

    使用 ffprobe（优先）或 ffmpeg 解析时长。

    Args:
        audio_path: 音频文件路径。

    Returns:
        时长（秒），失败返回 0.0。
    """
    if not os.path.isfile(audio_path):
        logger.error(f"get_audio_duration: 文件不存在: {audio_path}")
        return 0.0

    # 尝试 ffprobe（更快更准）
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError) as e:
            logger.debug(f"ffprobe 获取时长失败: {e}")

    # fallback: ffmpeg 从 stderr 解析 Duration
    ffmpeg = get_ffmpeg_path()
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-i", audio_path, "-f", "null", "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            # Duration 在 stderr 中: "Duration: HH:MM:SS.xx"
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
            if match:
                h, m, s = match.groups()
                return int(h) * 3600 + int(m) * 60 + float(s)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"ffmpeg 获取时长失败: {e}")

    logger.warning(f"获取音频时长失败: {audio_path}")
    return 0.0


def extract_audio_to_temp(video_path: str) -> Optional[str]:
    """从视频中抽取音频到临时文件，返回临时文件路径。

    调用方负责在使用完毕后删除临时文件。

    Args:
        video_path: 输入视频文件路径。

    Returns:
        临时 WAV 文件路径，失败返回 None。
    """
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="audio_preprocess_")
        os.close(fd)  # 关闭文件描述符，让 ffmpeg 写入

        if extract_audio(video_path, tmp_path):
            return tmp_path
        else:
            # 清理失败的临时文件
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return None
    except Exception as e:
        logger.error(f"extract_audio_to_temp 异常: {e}", exc_info=True)
        return None


def extract_audio_to_array(video_path: str) -> Optional[tuple]:
    """从视频中抽取音频到内存，返回 (audio_float32, sample_rate)。

    不写磁盘，通过 FFmpeg pipe 直接捕获 WAV 字节流。

    Args:
        video_path: 输入视频文件路径。

    Returns:
        (audio_float32_numpy_array, 16000) 或 None。
    """
    ffmpeg = get_ffmpeg_path()
    if ffmpeg is None:
        logger.error("extract_audio_to_array 失败: ffmpeg 不可用")
        return None

    if not os.path.isfile(video_path):
        logger.error(f"extract_audio_to_array 失败: 视频文件不存在: {video_path}")
        return None

    cmd = [
        ffmpeg,
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1",
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        wav_bytes, _ = proc.communicate(timeout=300)
        if proc.returncode != 0:
            logger.error(f"ffmpeg pipe 抽取失败 (code={proc.returncode})")
            return None
        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        return audio, sr
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.error(f"ffmpeg pipe 抽取超时 (>300s): {video_path}")
        return None
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        logger.error(f"extract_audio_to_array 异常: {e}", exc_info=True)
        return None
