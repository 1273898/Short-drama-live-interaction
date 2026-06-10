"""规则引擎：从视频文件中提取音频/视频特征，识别候选高光点（优化版）"""

import os
import sys
import cv2
import numpy as np
import subprocess
import tempfile
import shutil
import threading
from typing import List, Dict, Optional

# ffmpeg 可用性检测（同时检查 conda 环境 Scripts 目录）
_ffmpeg_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
if _ffmpeg_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

if not shutil.which("ffmpeg"):
    print("[WARNING] ffmpeg not found in PATH. 音频特征提取和视频转码将不可用。", file=sys.stderr)
    print("[WARNING] 安装方式: pip install imageio-ffmpeg 或从 https://ffmpeg.org/download.html 下载并加入 PATH", file=sys.stderr)

# 模块级权重常量
_AUDIO_WEIGHT = 0.6
_VIDEO_WEIGHT = 0.4
_weights_lock = threading.Lock()


def set_weights(audio_weight: float, video_weight: float):
    """动态调整音频/视觉权重（线程安全）"""
    global _AUDIO_WEIGHT, _VIDEO_WEIGHT
    total = audio_weight + video_weight
    with _weights_lock:
        _AUDIO_WEIGHT = audio_weight / total
        _VIDEO_WEIGHT = video_weight / total


def _extract_audio_to_wav(video_path: str) -> Optional[str]:
    """使用 ffmpeg 从视频中提取音频为 WAV"""
    if not shutil.which("ffmpeg"):
        return None

    fd, temp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-ar", "22050", "-ac", "1", "-f", "wav", "-y", temp_path
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            return None
        return temp_path
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None


def _load_audio(video_path: str):
    """加载音频，优先使用 soundfile，回退到 librosa"""
    audio_path = _extract_audio_to_wav(video_path)
    temp_file = audio_path is not None

    try:
        if audio_path:
            try:
                import soundfile as sf
                y, sr = sf.read(audio_path, dtype='float32')
                if len(y.shape) > 1:
                    y = y.mean(axis=1)
                return y, sr
            except ImportError:
                pass

        import librosa
        y, sr = librosa.load(video_path, sr=22050)
        return y, sr
    finally:
        if temp_file and audio_path and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass


def extract_audio_highlights(video_path: str) -> List[Dict]:
    """提取音频特征，检测音量突增点（优化版：滑动窗口 + 多特征）"""
    highlights = []
    try:
        y, sr = _load_audio(video_path)

        import librosa

        # 计算 RMS 和频谱质心
        hop_length = 512
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        spectral_cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)

        # 转换为 dB
        rms_db = 20 * np.log10(rms + 1e-10)

        # 滑动窗口参数
        window_sec = 2.0
        step_sec = 0.5
        window_frames = int(window_sec * sr / hop_length)
        step_frames = int(step_sec * sr / hop_length)

        # 第一遍扫描：获取频谱变化最大值用于归一化
        spectral_deltas = []
        for i in range(0, len(rms_db) - window_frames, step_frames):
            spec_window = spectral_cent[i:i + window_frames]
            if len(spec_window) >= 2:
                spectral_deltas.append(np.max(np.abs(np.diff(spec_window))))
        spectral_max = max(spectral_deltas) if spectral_deltas else 1.0
        spectral_max = max(spectral_max, 1.0)

        # 第二遍扫描：综合评分
        for i in range(0, len(rms_db) - window_frames, step_frames):
            window_rms = rms_db[i:i + window_frames]
            window_spec = spectral_cent[i:i + window_frames]

            if len(window_rms) < 2:
                continue

            # 特征1：音量变化
            db_change = np.abs(np.diff(window_rms))
            max_db_change = np.max(db_change)

            # 特征2：频谱变化率
            spec_delta = np.max(np.abs(np.diff(window_spec))) if len(window_spec) >= 2 else 0

            # 特征3：静音后爆发
            silence_threshold = -40
            has_silence_burst = False
            if np.any(window_rms < silence_threshold):
                silence_indices = np.where(window_rms < silence_threshold)[0]
                if len(silence_indices) > 0:
                    last_silence = silence_indices[-1]
                    if last_silence < len(window_rms) - 1:
                        post_silence = window_rms[last_silence + 1:]
                        if len(post_silence) > 0 and np.max(post_silence) - window_rms[last_silence] > 15:
                            has_silence_burst = True

            # 综合评分
            volume_score = max_db_change / 30.0
            spectral_score = spec_delta / spectral_max
            burst_score = 1.0 if has_silence_burst else 0.0

            confidence = min(0.5 * volume_score + 0.3 * spectral_score + 0.2 * burst_score, 1.0)

            if confidence > 0.5:  # 最低阈值（提高以减少低质量候选）
                timestamp_idx = i + np.argmax(db_change)
                if timestamp_idx < len(times):
                    highlights.append({
                        "timestamp": float(times[timestamp_idx]),
                        "confidence": float(confidence),
                        "source": "audio"
                    })

    except Exception as e:
        print(f"音频特征提取失败: {e}")
    return highlights


def extract_video_highlights(video_path: str) -> List[Dict]:
    """提取视频特征，检测画面剧烈变化（优化版：FPS自适应 + 光流 + 色彩 + 帧数限制）"""
    import logging
    _logger = logging.getLogger(__name__)

    highlights = []
    MAX_FRAMES = 1000  # 最大采样帧数
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            fps = 30

        # 根据视频时长自适应采样间隔，确保不超过 MAX_FRAMES
        sample_interval = max(1, int(fps / 2))  # 默认每秒2帧
        estimated_samples = total_frames / sample_interval if sample_interval > 0 else total_frames
        if estimated_samples > MAX_FRAMES:
            sample_interval = max(1, total_frames // MAX_FRAMES)

        duration_sec = total_frames / fps if fps > 0 else 0
        _logger.info(f"视频分析: {duration_sec:.0f}s, {total_frames} 帧, 采样间隔={sample_interval}, 预计采样 {total_frames // sample_interval} 帧")

        prev_gray = None
        prev_hsv = None
        frame_idx = 0
        sampled_count = 0
        last_progress_pct = 0

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_interval == 0:
                sampled_count += 1

                # 进度日志：每处理 10% 输出一次
                if total_frames > 0:
                    progress_pct = int(frame_idx / total_frames * 100)
                    if progress_pct >= last_progress_pct + 10:
                        _logger.info(f"视频分析进度: {progress_pct}% ({sampled_count} 帧已采样)")
                        last_progress_pct = progress_pct - (progress_pct % 10) + 10

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (320, 240))
                hsv = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2HSV)

                if prev_gray is not None:
                    # 特征1：帧差分
                    diff = cv2.absdiff(prev_gray, gray)
                    change_pixels = np.sum(diff > 30) / diff.size

                    # 特征2：运动强度（光流）
                    motion_magnitude = 0.0
                    if change_pixels > 0.05:  # 只在有明显变化时计算光流
                        try:
                            flow = cv2.calcOpticalFlowFarneback(
                                prev_gray, gray, None,
                                pyr_scale=0.5, levels=3, winsize=15,
                                iterations=3, poly_n=5, poly_sigma=1.2, flags=0
                            )
                            motion_magnitude = float(np.mean(
                                np.sqrt(flow[..., 0]**2 + flow[..., 1]**2)
                            ))
                        except Exception:
                            pass

                    # 特征3：色彩饱和度变化
                    sat_delta = 0.0
                    if prev_hsv is not None:
                        sat_delta = abs(float(np.mean(prev_hsv[:, :, 1])) - float(np.mean(hsv[:, :, 1])))

                    # 综合评分
                    frame_score = change_pixels / 0.5
                    motion_score = motion_magnitude / 50.0
                    color_score = sat_delta / 128.0

                    confidence = min(0.4 * frame_score + 0.4 * motion_score + 0.2 * color_score, 1.0)

                    if confidence > 0.5:  # 最低阈值（提高以减少低质量候选）
                        timestamp = frame_idx / fps
                        highlights.append({
                            "timestamp": float(timestamp),
                            "confidence": float(confidence),
                            "source": "video"
                        })

                prev_gray = gray
                prev_hsv = hsv
            frame_idx += 1

        cap.release()
        _logger.info(f"视频分析完成: {sampled_count} 帧采样, 发现 {len(highlights)} 个候选点")
    except Exception as e:
        _logger.error(f"视频特征提取失败: {e}")
    return highlights


def _fuse_multimodal(audio_highlights: List[Dict], video_highlights: List[Dict]) -> List[Dict]:
    """多模态融合：时序对齐 + 加权合并"""
    # 合并并按时间排序
    all_highlights = audio_highlights + video_highlights
    all_highlights.sort(key=lambda x: x["timestamp"])

    if not all_highlights:
        return []

    fused = []
    used = set()
    window_size = 2.0  # ±2秒窗口

    for i, h in enumerate(all_highlights):
        if i in used:
            continue

        # 收集窗口内的所有检测结果
        window_audio = []
        window_video = []

        if h["source"] == "audio":
            window_audio.append(h)
        else:
            window_video.append(h)

        for j in range(i + 1, len(all_highlights)):
            if j in used:
                continue
            if all_highlights[j]["timestamp"] - h["timestamp"] <= window_size:
                if all_highlights[j]["source"] == "audio":
                    window_audio.append(all_highlights[j])
                else:
                    window_video.append(all_highlights[j])
                used.add(j)
            else:
                break

        used.add(i)

        # 计算融合置信度
        audio_conf = max([h["confidence"] for h in window_audio]) if window_audio else 0
        video_conf = max([h["confidence"] for h in window_video]) if window_video else 0

        if window_audio and window_video:
            confidence = audio_conf * _AUDIO_WEIGHT + video_conf * _VIDEO_WEIGHT
            source = "multimodal"
        elif window_audio:
            confidence = audio_conf * _AUDIO_WEIGHT
            source = "audio"
        else:
            confidence = video_conf * _VIDEO_WEIGHT
            source = "video"

        # 使用最高置信度检测点的时间戳
        best_audio = max(window_audio, key=lambda x: x["confidence"]) if window_audio else None
        best_video = max(window_video, key=lambda x: x["confidence"]) if window_video else None

        if best_audio and best_video:
            timestamp = best_audio["timestamp"] if best_audio["confidence"] >= best_video["confidence"] else best_video["timestamp"]
        elif best_audio:
            timestamp = best_audio["timestamp"]
        else:
            timestamp = best_video["timestamp"]

        fused.append({
            "timestamp": float(timestamp),
            "confidence": float(confidence),
            "source": source
        })

    return fused


def analyze_video(video_path: str) -> List[Dict]:
    """分析视频，返回候选高光点列表（多模态融合版）"""
    audio_highlights = extract_audio_highlights(video_path)
    video_highlights = extract_video_highlights(video_path)

    # 如果任一引擎无结果，直接返回另一个
    if not audio_highlights:
        return video_highlights
    if not video_highlights:
        return audio_highlights

    # 多模态融合
    return _fuse_multimodal(audio_highlights, video_highlights)


async def analyze_video_async(video_path: str) -> List[Dict]:
    """异步版本：音频和视频分析并行执行，带超时保护"""
    import asyncio
    import logging
    _logger = logging.getLogger(__name__)

    loop = asyncio.get_running_loop()

    # 每个任务最多 150 秒
    audio_task = asyncio.wait_for(
        loop.run_in_executor(None, extract_audio_highlights, video_path),
        timeout=150
    )
    video_task = asyncio.wait_for(
        loop.run_in_executor(None, extract_video_highlights, video_path),
        timeout=150
    )

    # 收集结果，忽略超时
    results = await asyncio.gather(audio_task, video_task, return_exceptions=True)

    audio_highlights = results[0] if isinstance(results[0], list) else []
    video_highlights = results[1] if isinstance(results[1], list) else []

    # 记录超时情况
    if isinstance(results[0], asyncio.TimeoutError):
        _logger.warning(f"音频分析超时: {video_path}")
    elif isinstance(results[0], Exception):
        _logger.warning(f"音频分析异常: {video_path} - {results[0]}")

    if isinstance(results[1], asyncio.TimeoutError):
        _logger.warning(f"视频分析超时: {video_path}")
    elif isinstance(results[1], Exception):
        _logger.warning(f"视频分析异常: {video_path} - {results[1]}")

    if not audio_highlights:
        return video_highlights
    if not video_highlights:
        return audio_highlights
    return _fuse_multimodal(audio_highlights, video_highlights)


def analyze_video_sync(video_path: str) -> List[Dict]:
    """同步版本（保持兼容）"""
    return analyze_video(video_path)
