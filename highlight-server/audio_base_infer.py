"""VAD 人声段检测与基础声学特征提取

依赖：
- model_loader：Silero-VAD 模型
- audio_preprocess：输出的 16kHz 单声道 WAV

特征：
- RMS 能量：反映音量大小
- 频谱通量（Spectral Flux）：反映声音突变/爆发力
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# 常量
SAMPLE_RATE = 16000  # 与 audio_preprocess 输出一致
FRAME_MS = 200       # 特征提取滑动窗口（毫秒）
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)  # 200ms = 3200 samples
FFT_SIZE = 1024      # FFT 窗口大小（频谱通量用）
HOP_SAMPLES = 512    # FFT hop size


def detect_speech_segments(audio_path: str = None, audio_data: tuple = None) -> list[dict]:
    """使用 Silero-VAD 检测音频中的人声段。

    Args:
        audio_path: 16kHz 单声道 WAV 文件路径（与 audio_data 二选一）。
        audio_data: (audio_float32, sample_rate) 元组，优先于 audio_path。

    Returns:
        人声段列表，每项包含：
        - start: 开始时间（秒）
        - end: 结束时间（秒）
        - duration: 持续时长（秒）
        失败返回空列表。
    """
    try:
        import torch
    except ImportError:
        logger.error("torch 未安装，VAD 检测不可用")
        return []

    try:
        from model_loader import get_manager
    except ImportError:
        logger.error("model_loader 模块导入失败")
        return []

    # 加载模型
    manager = get_manager()
    try:
        model = manager.load_silero_vad()
    except RuntimeError as e:
        logger.error(f"Silero-VAD 加载失败: {e}")
        return []

    get_speech_timestamps = manager.get_vad_util("get_speech_timestamps")
    if get_speech_timestamps is None:
        logger.error("get_speech_timestamps 不可用")
        return []

    try:
        if audio_data is not None:
            data_np, sr = audio_data
        else:
            import soundfile as sf
            data_np, sr = sf.read(audio_path, dtype="float32")

        # 确保是单声道
        if data_np.ndim > 1:
            data_np = data_np.mean(axis=1)

        # 确保采样率正确
        if sr != SAMPLE_RATE:
            import torchaudio
            logger.warning(f"音频采样率 {sr} != {SAMPLE_RATE}，进行重采样")
            waveform = torch.from_numpy(data_np).unsqueeze(0)
            resampler = torchaudio.transforms.Resample(sr, SAMPLE_RATE)
            waveform = resampler(waveform)
            audio = waveform.squeeze(0)
            sr = SAMPLE_RATE
        else:
            audio = torch.from_numpy(data_np)

        # VAD 检测
        speech_timestamps = get_speech_timestamps(
            audio,
            model,
            sampling_rate=sr,
            threshold=0.5,           # VAD 置信度阈值
            min_speech_duration_ms=250,   # 最短人声段 250ms
            min_silence_duration_ms=100,  # 最短静音段 100ms（合并间隔）
            speech_pad_ms=30,        # 人声段前后填充 30ms
            return_seconds=False,    # 返回采样点索引
        )

        if not speech_timestamps:
            src = audio_path or "array"
            logger.info(f"未检测到人声段: {src}")
            return []

        # 转换为秒
        segments = []
        for ts in speech_timestamps:
            start = ts["start"] / sr
            end = ts["end"] / sr
            segments.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })

        logger.info(f"检测到 {len(segments)} 个人声段, "
                     f"总时长 {sum(s['duration'] for s in segments):.1f}s")
        return segments

    except Exception as e:
        logger.error(f"VAD 检测异常: {e}", exc_info=True)
        return []


def _compute_rms_energy(audio: np.ndarray, frame_size: int = FRAME_SAMPLES) -> np.ndarray:
    """计算 RMS 能量（向量化）。

    Args:
        audio: float32 音频数组。
        frame_size: 帧大小（采样点数）。

    Returns:
        每帧的 RMS 值数组。
    """
    # 截断尾部不足一帧的部分
    n_frames = len(audio) // frame_size
    if n_frames == 0:
        return np.array([], dtype=np.float32)

    # reshape 为 (n_frames, frame_size) 后按行求 RMS
    frames = audio[:n_frames * frame_size].reshape(n_frames, frame_size)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return rms.astype(np.float32)


def _compute_spectral_flux(audio: np.ndarray, n_fft: int = FFT_SIZE,
                           hop_length: int = HOP_SAMPLES) -> np.ndarray:
    """计算频谱通量（向量化）。

    频谱通量 = 相邻帧频谱幅度之差的 L2 范数，反映声音的突变程度。

    Args:
        audio: float32 音频数组。
        n_fft: FFT 窗口大小。
        hop_length: 帧移（采样点数）。

    Returns:
        每帧的频谱通量值数组（长度 = n_frames - 1）。
    """
    if len(audio) < n_fft:
        return np.array([], dtype=np.float32)

    # 计算帧数
    n_frames = 1 + (len(audio) - n_fft) // hop_length
    if n_frames < 2:
        return np.array([], dtype=np.float32)

    # 构建 Hann 窗
    window = np.hanning(n_fft).astype(np.float32)

    # 使用 stride_tricks 构建帧矩阵（零拷贝视图）
    shape = (n_frames, n_fft)
    strides = (audio.strides[0] * hop_length, audio.strides[0])
    frames = np.lib.stride_tricks.as_strided(audio, shape=shape, strides=strides)

    # 加窗
    windowed = frames * window  # (n_frames, n_fft)

    # FFT 并取幅度谱
    spectrum = np.abs(np.fft.rfft(windowed, axis=1))  # (n_frames, n_fft//2+1)

    # 频谱通量：相邻帧幅度差的 L2 范数
    diff = np.diff(spectrum, axis=0)  # (n_frames-1, n_fft//2+1)
    flux = np.sqrt(np.sum(diff ** 2, axis=1))

    return flux.astype(np.float32)


def _time_to_frame_index(time_sec: float, frame_size: int = FRAME_SAMPLES,
                         sample_rate: int = SAMPLE_RATE) -> int:
    """时间（秒）转帧索引。"""
    return int(time_sec * sample_rate / frame_size)


def extract_acoustic_features(audio_path: str = None,
                               speech_segments: list[dict] = None,
                               audio_data: tuple = None) -> list[dict]:
    """对人声段提取基础声学特征。

    仅对人声段区域计算特征以节省 CPU 算力。

    Args:
        audio_path: 16kHz 单声道 WAV 文件路径（与 audio_data 二选一）。
        speech_segments: 人声段列表（来自 detect_speech_segments）。
        audio_data: (audio_float32, sample_rate) 元组，优先于 audio_path。

    Returns:
        特征时间序列，每项包含：
        - time: 帧中心时间（秒）
        - rms: RMS 能量值
        - spectral_flux: 频谱通量值
        - is_speech: 是否在人声段内（True/False）
        失败返回空列表。
    """
    if audio_data is not None:
        data_np, sr = audio_data
        if data_np.ndim > 1:
            data_np = data_np.mean(axis=1)
        if sr != SAMPLE_RATE:
            import torch
            import torchaudio
            waveform = torch.from_numpy(data_np).unsqueeze(0)
            resampler = torchaudio.transforms.Resample(sr, SAMPLE_RATE)
            waveform = resampler(waveform)
            audio = waveform.squeeze(0).numpy().astype(np.float32)
        else:
            audio = data_np
    else:
        audio = _load_audio_mono(audio_path)
    if audio is None or len(audio) == 0:
        return []

    # 计算 RMS 能量（整个音频，后续按人声段过滤）
    rms_all = _compute_rms_energy(audio)

    # 计算频谱通量（整个音频）
    flux_all = _compute_spectral_flux(audio)

    if len(rms_all) == 0:
        logger.warning("音频过短，无法提取特征")
        return []

    total_duration = len(audio) / SAMPLE_RATE
    n_rms_frames = len(rms_all)
    rms_frame_duration = FRAME_SAMPLES / SAMPLE_RATE  # 每帧时长（秒）

    # 为人声段构建快速查找集合（帧索引级别）
    speech_frame_indices = set()
    for seg in speech_segments:
        start_idx = _time_to_frame_index(seg["start"])
        end_idx = _time_to_frame_index(seg["end"]) + 1
        for idx in range(max(0, start_idx), min(n_rms_frames, end_idx)):
            speech_frame_indices.add(idx)

    # 构建特征序列（仅人声段帧）
    features = []
    for i in range(n_rms_frames):
        if i not in speech_frame_indices:
            continue

        time_sec = round(i * rms_frame_duration + rms_frame_duration / 2, 3)

        # RMS
        rms_val = float(rms_all[i])

        # 频谱通量：RMS 帧与 flux 帧的粒度不同，需要映射
        # flux 的帧率更高（hop=512, ~32ms/帧），RMS 帧率较低（200ms/帧）
        # 取对应时间窗口内的 flux 均值
        flux_start = int(time_sec * SAMPLE_RATE / HOP_SAMPLES)
        flux_end = int((time_sec + rms_frame_duration) * SAMPLE_RATE / HOP_SAMPLES)
        flux_start = max(0, flux_start)
        flux_end = min(len(flux_all), flux_end)

        if flux_start < flux_end:
            flux_val = float(np.mean(flux_all[flux_start:flux_end]))
        else:
            flux_val = 0.0

        features.append({
            "time": time_sec,
            "rms": round(rms_val, 6),
            "spectral_flux": round(flux_val, 6),
            "is_speech": True,
        })

    if features:
        avg_rms = np.mean([f["rms"] for f in features])
        avg_flux = np.mean([f["spectral_flux"] for f in features])
        logger.info(f"特征提取完成: {len(features)} 帧, "
                     f"avg_rms={avg_rms:.4f}, avg_flux={avg_flux:.4f}")
    else:
        logger.info("无特征帧（可能音频中无人声）")

    return features
