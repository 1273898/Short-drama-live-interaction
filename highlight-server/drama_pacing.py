"""剧情节奏感知模块：分析单集情绪变化曲线，识别情绪反转点

输入：torch_emotion_infer 输出的 emotion_result（含 timeline）。
输出：每个候选事件的节奏分（pacing_score）及反转点标记。
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 反转点置信度阈值
_REVERSAL_CONFIDENCE_THRESHOLD = 0.6

# 反转点加分
_REVERSAL_BONUS = 0.15


def detect_emotion_reversals(
    emotion_timeline: list[dict],
) -> list[dict]:
    """检测情绪反转点。

    反转点定义：相邻段情绪标签不同，且两段置信度均 > 0.6。

    Args:
        emotion_timeline: 情绪推理输出的 timeline，
            每项含 {start, end, time, intensity, label, confidence}。

    Returns:
        反转点列表 [{time, from_label, to_label, confidence}]。
        按时间排序。
    """
    if not emotion_timeline or len(emotion_timeline) < 2:
        return []

    # 按时间排序
    sorted_tl = sorted(emotion_timeline, key=lambda e: e["time"])

    reversals = []
    for i in range(1, len(sorted_tl)):
        prev = sorted_tl[i - 1]
        curr = sorted_tl[i]

        # 标签不同 + 双方置信度均 > 阈值
        if prev["label"] != curr["label"]:
            prev_conf = prev.get("confidence", 0.0)
            curr_conf = curr.get("confidence", 0.0)
            if prev_conf > _REVERSAL_CONFIDENCE_THRESHOLD and curr_conf > _REVERSAL_CONFIDENCE_THRESHOLD:
                # 反转时间取两段中间点
                reversal_time = (prev["time"] + curr["time"]) / 2.0
                avg_conf = (prev_conf + curr_conf) / 2.0
                reversals.append({
                    "time": round(reversal_time, 3),
                    "from_label": prev["label"],
                    "to_label": curr["label"],
                    "confidence": round(avg_conf, 4),
                })

    logger.info(f"情绪反转点检测: {len(reversals)} 个 (timeline {len(sorted_tl)} 段)")
    for r in reversals:
        logger.info(f"  反转: {r['from_label']}→{r['to_label']} @ {r['time']:.1f}s, conf={r['confidence']:.2f}")

    return reversals


def compute_pacing_score(
    peak_time: float,
    emotion_timeline: list[dict],
    tolerance: float = 5.0,
    reversals: Optional[list[dict]] = None,
) -> float:
    """计算单个候选事件的剧情节奏分。

    节奏分 ∈ [0, 1]：
    - 基础分：该时间点附近的情绪变化密度（反转点数量归一化）
    - 反转点加分：如果 peak_time 附近有反转点，直接加 _REVERSAL_BONUS

    Args:
        peak_time: 候选事件峰值时间（秒）。
        emotion_timeline: 情绪推理输出的 timeline。
        tolerance: 匹配容差（秒）。
        reversals: 预计算的反转点列表（可选，避免重复计算）。

    Returns:
        节奏分 ∈ [0, 1]。
    """
    if not emotion_timeline or len(emotion_timeline) < 2:
        return 0.0

    if reversals is None:
        reversals = detect_emotion_reversals(emotion_timeline)
    if not reversals:
        return 0.0

    # 统计 tolerance 范围内的反转点数
    nearby_count = sum(
        1 for r in reversals if abs(r["time"] - peak_time) <= tolerance
    )

    # 归一化：最多 3 个反转点封顶 → 1.0
    density_score = min(nearby_count / 3.0, 1.0)

    # 如果附近有反转点，加 bonus
    has_nearby_reversal = nearby_count > 0
    bonus = _REVERSAL_BONUS if has_nearby_reversal else 0.0

    raw = density_score + bonus
    return round(min(raw, 1.0), 4)


def compute_pacing_scores(
    acoustic_events: list[dict],
    emotion_timeline: Optional[list[dict]],
    tolerance: float = 5.0,
) -> list[float]:
    """批量计算候选事件的节奏分。

    Args:
        acoustic_events: event_cluster 输出的候选事件列表。
        emotion_timeline: 情绪推理输出的 timeline。
        tolerance: 匹配容差（秒）。

    Returns:
        与 acoustic_events 等长的节奏分列表。
        情绪 timeline 不可用时返回全零列表。
    """
    if not emotion_timeline or len(emotion_timeline) < 2:
        return [0.0] * len(acoustic_events)

    # 预计算反转点，避免每个事件重复计算
    reversals = detect_emotion_reversals(emotion_timeline)
    if not reversals:
        return [0.0] * len(acoustic_events)

    return [
        compute_pacing_score(evt["peak_time"], emotion_timeline, tolerance, reversals=reversals)
        for evt in acoustic_events
    ]
