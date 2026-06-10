"""滑动窗口聚合：从声学特征中提取候选高光事件

废弃原方案的 DBSCAN，改用简单高效的滑动窗口 + 峰值检测算法。
输入：audio_base_infer 输出的特征时间序列。
输出：候选高光事件列表（start_time, end_time, peak_time, acoustic_score）。

增强：峰值检测后支持基于情绪标签的剧情连贯性合并。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 权重常量：RMS 能量 vs 频谱通量
_RMS_WEIGHT = 0.6
_FLUX_WEIGHT = 0.4


def _normalize_minmax(arr: np.ndarray) -> np.ndarray:
    """Min-Max 归一化到 [0, 1]。

    全部相同时返回零数组（避免除零）。
    """
    vmin, vmax = arr.min(), arr.max()
    if vmax - vmin < 1e-10:
        return np.zeros_like(arr)
    return (arr - vmin) / (vmax - vmin)


def _assign_emotion_to_events(
    events: list[dict],
    emotion_timeline: list[dict],
    tolerance: float = 5.0,
) -> list[dict]:
    """为候选事件附加最近的情绪标签（用于后续连贯性合并）。

    Args:
        events: 候选事件列表（含 peak_time）。
        emotion_timeline: 情绪推理 timeline（含 time, label, confidence）。
        tolerance: 时间匹配容差（秒）。

    Returns:
        原地修改 events，添加 _emotion_label 字段，返回 events。
    """
    if not emotion_timeline:
        return events

    sorted_tl = sorted(emotion_timeline, key=lambda e: e["time"])
    tl_times = [e["time"] for e in sorted_tl]

    for evt in events:
        peak = evt["peak_time"]
        best_dist = float("inf")
        best_label = None
        for j, entry in enumerate(sorted_tl):
            dist = abs(tl_times[j] - peak)
            if dist <= tolerance and dist < best_dist:
                best_dist = dist
                best_label = entry.get("label")
        evt["_emotion_label"] = best_label

    return events


def merge_coherent_events(
    events: list[dict],
    emotion_timeline: Optional[list[dict]] = None,
    merge_gap: float = 10.0,
    coherence_bonus: float = 0.05,
) -> list[dict]:
    """剧情连贯性合并：对间隔 < merge_gap 且情绪标签相同的相邻候选事件进行合并。

    合并规则：
    - 间隔 < merge_gap 秒 且 情绪标签相同 → 合并为一个更宽的区间
    - 合并后的 score = max(scores) + coherence_bonus
    - 区间取两段的并集（start_time 取 min，end_time 取 max）
    - peak_time 保留分数更高的那段

    Args:
        events: 候选事件列表（已按 acoustic_score 降序）。
        emotion_timeline: 情绪推理 timeline（可选）。
        merge_gap: 合并间隔阈值（秒），默认 10s。
        coherence_bonus: 连贯性奖励分，默认 0.05。

    Returns:
        合并后的候选事件列表。
    """
    if not events or len(events) < 2:
        return events

    if not emotion_timeline:
        return events

    # 附加情绪标签
    _assign_emotion_to_events(events, emotion_timeline)

    # 按时间排序以便相邻合并
    time_sorted = sorted(events, key=lambda x: x["peak_time"])

    merged = [time_sorted[0]]
    for evt in time_sorted[1:]:
        prev = merged[-1]
        prev_label = prev.get("_emotion_label")
        curr_label = evt.get("_emotion_label")

        gap = abs(evt["peak_time"] - prev["peak_time"])
        if gap < merge_gap and prev_label and curr_label and prev_label == curr_label:
            # 合并：扩展区间，score 取 max（bonus 只加一次，不累加）
            prev["start_time"] = min(prev["start_time"], evt["start_time"])
            prev["end_time"] = max(prev["end_time"], evt["end_time"])
            if evt["acoustic_score"] > prev["acoustic_score"]:
                prev["peak_time"] = evt["peak_time"]
            prev["acoustic_score"] = round(
                max(prev["acoustic_score"], evt["acoustic_score"]), 4
            )
            # bonus 只在首次合并时加（通过标记避免累加）
            if not prev.get("_merged_bonus_applied"):
                prev["acoustic_score"] = round(prev["acoustic_score"] + coherence_bonus, 4)
                prev["_merged_bonus_applied"] = True
        else:
            merged.append(evt)

    # 清理临时字段
    for evt in merged:
        evt.pop("_emotion_label", None)
        evt.pop("_merged_bonus_applied", None)

    if len(merged) < len(events):
        logger.info(f"剧情连贯性合并: {len(events)} → {len(merged)} 个候选事件")

    return merged


def detect_acoustic_events(
    features: list[dict],
    window_size: float = 5.0,
    step_size: float = 1.0,
    min_score_threshold: float = 0.3,
) -> list[dict]:
    """滑动窗口聚合，从声学特征中提取候选高光事件。

    Args:
        features: 特征列表，每项含 {time, rms, spectral_flux, is_speech}。
        window_size: 滑动窗口大小（秒），默认 5s。
        step_size: 滑动步长（秒），默认 1s。
        min_score_threshold: 最低分数阈值，默认 0.3。

    Returns:
        候选事件列表 [{start_time, end_time, peak_time, acoustic_score}]，
        按 acoustic_score 降序排列。无有效事件返回空列表。
    """
    if not features:
        logger.info("输入特征为空，跳过事件检测")
        return []

    # 提取时间序列
    times = np.array([f["time"] for f in features], dtype=np.float32)
    rms_arr = np.array([f["rms"] for f in features], dtype=np.float32)
    flux_arr = np.array([f["spectral_flux"] for f in features], dtype=np.float32)

    if len(times) == 0:
        return []

    # Min-Max 归一化
    norm_rms = _normalize_minmax(rms_arr)
    norm_flux = _normalize_minmax(flux_arr)

    # 单帧声学突变分
    frame_scores = _RMS_WEIGHT * norm_rms + _FLUX_WEIGHT * norm_flux

    total_duration = float(times[-1]) + (times[1] - times[0] if len(times) > 1 else 0.5)
    frame_interval = float(times[1] - times[0]) if len(times) > 1 else 0.2

    # --- 滑动窗口计算平均分 ---
    window_scores: list[dict] = []
    t = 0.0
    while t < total_duration:
        t_end = t + window_size
        # 找落在窗口内的帧索引
        mask = (times >= t) & (times < t_end)
        if mask.any():
            avg_score = float(frame_scores[mask].mean())
            # 窗口中心时间作为代表
            peak_idx = mask.nonzero()[0][frame_scores[mask].argmax()]
            window_scores.append({
                "start": t,
                "end": min(t_end, total_duration),
                "center": (t + t_end) / 2,
                "score": avg_score,
                "peak_time": float(times[peak_idx]),
                "peak_score": float(frame_scores[peak_idx]),
            })
        t += step_size

    if not window_scores:
        logger.info("滑动窗口未产生有效分数")
        return []

    # --- 峰值检测：局部最大值 ---
    scores_arr = np.array([w["score"] for w in window_scores])
    peaks: list[int] = []
    for i in range(len(scores_arr)):
        if scores_arr[i] < min_score_threshold:
            continue
        # 检查是否为局部最大值
        left_ok = (i == 0) or (scores_arr[i] >= scores_arr[i - 1])
        right_ok = (i == len(scores_arr) - 1) or (scores_arr[i] >= scores_arr[i + 1])
        if left_ok and right_ok:
            peaks.append(i)

    if not peaks:
        logger.info(f"未找到超过阈值 {min_score_threshold} 的峰值事件")
        return []

    # --- 事件合并：间隔 < window_size 的峰值只保留最高分 ---
    peak_events = [window_scores[i] for i in peaks]
    # 按时间排序后单次遍历合并（O(n log n) 排序 + O(n) 遍历）
    peak_events.sort(key=lambda x: x["peak_time"])

    half_w = window_size / 2
    merged: list[dict] = []
    for evt in peak_events:
        if merged and evt["peak_time"] - merged[-1]["peak_time"] < window_size:
            # 间隔 < window_size，保留分数更高的
            if evt["score"] > merged[-1]["score"]:
                merged[-1] = evt
        else:
            merged.append(evt)

    # 构建事件区间：以 peak_time 为中心，扩展 window_size/2 到两侧
    for evt in merged:
        start_time = max(0.0, evt["peak_time"] - half_w)
        end_time = min(total_duration, evt["peak_time"] + half_w)
        evt["start_time"] = round(start_time, 3)
        evt["end_time"] = round(end_time, 3)
        evt["peak_time"] = round(evt["peak_time"], 3)
        evt["acoustic_score"] = round(evt["score"], 4)

    # 合并重叠区间（已按时间排序，直接贪心合并）
    merged.sort(key=lambda x: x["start_time"])
    deduped = [merged[0]]
    for evt in merged[1:]:
        prev = deduped[-1]
        if evt["start_time"] <= prev["end_time"]:
            # 重叠：保留分数更高的，扩展区间
            if evt["acoustic_score"] > prev["acoustic_score"]:
                prev["peak_time"] = evt["peak_time"]
                prev["acoustic_score"] = evt["acoustic_score"]
            prev["end_time"] = max(prev["end_time"], evt["end_time"])
        else:
            deduped.append(evt)
    merged = deduped

    # 按分数降序排列
    merged.sort(key=lambda x: x["acoustic_score"], reverse=True)

    logger.info(f"滑动窗口聚合完成: {len(features)} 帧 -> {len(merged)} 个候选事件")
    for i, evt in enumerate(merged):
        logger.info(f"  事件[{i}]: peak={evt['peak_time']:.1f}s, "
                     f"score={evt['acoustic_score']:.3f}, "
                     f"range=[{evt['start_time']:.1f}, {evt['end_time']:.1f}]")

    return merged
