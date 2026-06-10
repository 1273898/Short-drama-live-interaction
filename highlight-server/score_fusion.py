"""融合打分与结构对齐：将候选事件转化为最终高光点

严格对齐现有 ServerHighlightPoint 结构，确保客户端零改动。
"""
from __future__ import annotations

import bisect
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 分数映射参数：acoustic_score (0~1) -> 业务分数 (1~5)
_SCORE_MIN = 1.0
_SCORE_MAX = 5.0

# 分级阈值
_L1_THRESHOLD = 3.4   # >= 3.4 分为 L1
_L2_THRESHOLD = 2.0   # >= 2 分为 L2

# 区间宽度下限（秒）
# 客户端 VideoPlayer 的高光检测基于区间状态机：
#   pos in [startTime - tolerance, endTime + tolerance]
# 其中 tolerance 由 defaultHalfWindowMs=2000 控制。
# 若区间过窄 (< 3s)，客户端可能因 ExoPlayer 位置轮询间隔 (250ms) 或
# seek 精度问题而错过检测窗口。因此强制保证区间 >= 3s。
_MIN_INTERVAL_WIDTH = 3.0

# 相邻高光点最小间隔比例：间隔 >= 剧集时长 × 此值
_MIN_SPACING_RATIO = 0.2

# 边界排除比例：高光点 timestamp 不能落在剧集前/后此比例内
_BOUNDARY_RATIO = 0.05


def _filter_by_boundary(
    highlights: list[dict],
    video_duration: float,
) -> list[dict]:
    """过滤落在剧集前/后 _BOUNDARY_RATIO 内的高光点。

    兜底：如果过滤后无高光点，保留得分最高的 1 个。
    """
    if not highlights or video_duration <= 0:
        return highlights

    start_bound = video_duration * _BOUNDARY_RATIO
    end_bound = video_duration * (1 - _BOUNDARY_RATIO)

    kept = [hl for hl in highlights
            if start_bound <= hl["timestamp"] <= end_bound]

    if len(kept) < len(highlights):
        dropped = len(highlights) - len(kept)
        logger.info(f"边界过滤: {len(highlights)} → {len(kept)} 个高光点 "
                    f"(丢弃 {dropped} 个, 排除 [0,{start_bound:.1f}] 和 [{end_bound:.1f},{video_duration:.1f}])")

    # 兜底：边界过滤后无高光点时，保留得分最高的 1 个
    if not kept and highlights:
        best = max(highlights, key=lambda h: h["score"])
        kept = [best]
        logger.info(f"边界过滤兜底: 保留得分最高的 1 个高光点 (t={best['timestamp']:.1f}s, score={best['score']:.2f})")

    return kept


def _filter_by_spacing(
    highlights: list[dict],
    video_duration: float,
) -> list[dict]:
    """过滤间隔 < 剧集时长 × _MIN_SPACING_RATIO 的高光点。

    贪心策略：按分数降序依次保留，跳过与已保留高光点间隔不足的候选。
    输入 highlights 需已按 timestamp 排序。
    """
    if len(highlights) < 2 or video_duration <= 0:
        return highlights

    min_gap = video_duration * _MIN_SPACING_RATIO
    # 按分数降序处理，优先保留高分
    sorted_by_score = sorted(highlights, key=lambda h: h["score"], reverse=True)
    kept: list[dict] = []
    for hl in sorted_by_score:
        ts = hl["timestamp"]
        if all(abs(ts - k["timestamp"]) >= min_gap for k in kept):
            kept.append(hl)
    kept.sort(key=lambda h: h["timestamp"])
    if len(kept) < len(highlights):
        dropped = len(highlights) - len(kept)
        logger.info(f"间隔过滤: {len(highlights)} → {len(kept)} 个高光点 "
                    f"(丢弃 {dropped} 个, 最小间隔 {min_gap:.1f}s)")
    return kept


def _map_score(acoustic_score: float) -> float:
    """将 acoustic_score (0~1) 映射到业务分数 (1~5)。"""
    return round(_SCORE_MIN + acoustic_score * (_SCORE_MAX - _SCORE_MIN), 2)


def _classify(score: float) -> Optional[str]:
    """根据业务分数分级。低于 L2 阈值返回 None（丢弃）。"""
    if score >= _L1_THRESHOLD:
        return "L1"
    elif score >= _L2_THRESHOLD:
        return "L2"
    return None


def _enforce_interval_width(
    start_time: float,
    end_time: float,
    peak_time: float,
    video_duration: float,
) -> tuple[float, float]:
    """强制保证区间宽度 >= _MIN_INTERVAL_WIDTH。

    如果 end_time - start_time < _MIN_INTERVAL_WIDTH，以 peak_time 为中心
    向两侧扩展直到满足 3s，同时确保不越界 [0, video_duration]。

    为什么需要这个约束：
    客户端 VideoPlayer 的高光检测逻辑是基于区间状态机的——
    它检查当前播放位置是否落在 [startTime - halfWindow, endTime + halfWindow] 内。
    halfWindow 在 startTime==endTime 时为 2000ms，否则为 500ms。
    如果区间过窄（比如只有 0.5s），加上 500ms 容差也只有 1.5s 的检测窗口，
    在 ExoPlayer 250ms 轮询间隔下极易错过。
    强制 >= 3s 区间确保检测窗口至少 4s（3s + 2×500ms），足够可靠触发。
    """
    current_width = end_time - start_time
    if current_width >= _MIN_INTERVAL_WIDTH:
        return start_time, end_time

    # 以 peak_time 为中心扩展
    half = _MIN_INTERVAL_WIDTH / 2.0
    new_start = peak_time - half
    new_end = peak_time + half

    # 边界修正：不能小于 0
    if new_start < 0:
        new_start = 0.0
        new_end = _MIN_INTERVAL_WIDTH

    # 边界修正：不能大于 video_duration
    if video_duration > 0 and new_end > video_duration:
        new_end = video_duration
        new_start = max(0.0, new_end - _MIN_INTERVAL_WIDTH)

    # 极端情况：video_duration < _MIN_INTERVAL_WIDTH
    if video_duration > 0 and video_duration < _MIN_INTERVAL_WIDTH:
        new_start = 0.0
        new_end = video_duration

    return round(new_start, 3), round(new_end, 3)


def fuse_and_format_highlights(
    acoustic_events: list[dict],
    video_duration: float,
    max_highlights: int = 2,
) -> list[dict]:
    """将候选事件转化为最终高光点，严格对齐 ServerHighlightPoint 结构。

    流程：分数映射 → L1/L2 分级 → 低分过滤 → 区间宽度约束 → 数量截断。

    阶段 1 降级说明：
    - subtitle_context 填空字符串（无 ASR/字幕数据）
    - comments 填空列表（后续由 roast_generator 填充）
    - ai_source 固定为 "audio_acoustic"（纯声学来源）

    Args:
        acoustic_events: event_cluster 输出的候选事件列表。
        video_duration: 视频总时长（秒）。
        max_highlights: 最多保留的高光点数，默认 2。

    Returns:
        ServerHighlightPoint 格式的高光点列表（按 timestamp 排序）。
        无有效高光返回空列表。
    """
    if not acoustic_events:
        logger.info("无候选事件，返回空结果")
        return []

    # 1. 分数映射 + 分级 + 过滤
    candidates = []
    for evt in acoustic_events:
        score = _map_score(evt["acoustic_score"])
        level = _classify(score)
        if level is None:
            logger.debug(f"丢弃低分事件: peak={evt['peak_time']:.1f}s, "
                         f"acoustic={evt['acoustic_score']:.3f}, score={score}")
            continue
        candidates.append({**evt, "score": score, "type": level})

    if not candidates:
        logger.info("所有候选事件均低于 L2 阈值，返回空结果")
        return []

    # 2. 数量截断：按分数降序取前 max_highlights 个
    candidates.sort(key=lambda x: x["score"], reverse=True)
    candidates = candidates[:max_highlights]

    # 3. 构建最终输出，严格对齐 ServerHighlightPoint
    highlights = []
    for evt in candidates:
        start_time, end_time = _enforce_interval_width(
            evt["start_time"], evt["end_time"], evt["peak_time"], video_duration
        )
        highlights.append({
            "timestamp": evt["peak_time"],
            "start_time": start_time,
            "end_time": end_time,
            "type": evt["type"],
            "confidence": evt["acoustic_score"],
            "score": evt["score"],
            "subtitle_context": "",   # 阶段 1：无 ASR 数据
            "comments": [],           # 后续由 roast_generator 填充
            "ai_source": "audio_acoustic",
        })

    # 按 timestamp 排序（时间顺序）
    highlights.sort(key=lambda x: x["timestamp"])

    # 4. 间隔过滤：相邻高光点间隔 >= 剧集时长 × 20%
    highlights = _filter_by_spacing(highlights, video_duration)

    # 5. 边界过滤：排除前/后 5% 的高光点
    highlights = _filter_by_boundary(highlights, video_duration)

    logger.info(f"最终输出 {len(highlights)} 个高光点:")
    for i, h in enumerate(highlights):
        logger.info(f"  高光[{i}]: t={h['timestamp']:.1f}s, score={h['score']}, "
                     f"type={h['type']}, range=[{h['start_time']:.1f}, {h['end_time']:.1f}]")

    return highlights


# ── V2 融合（D9 升级：接入情绪 + 关键词 + 自适应权重） ──────────

# 一票否决阈值
_VETO_THRESHOLD = 0.15


def _compute_rule_score(
    evt: dict,
    video_duration: float,
    emotion_intensity: float,
    acoustic_score: float,
) -> float:
    """规则分计算（D13 扩充）。

    规则：
    1. 后半段加分：peak_time > duration × 0.8 → +0.15（短剧高潮常在结尾）
    2. 日常寒暄扣分：事件段 < 1.5s 且声学分 < 0.2 → -0.2（短低能片段=闲聊）
    3. 情绪×声学共振加分：emotion_intensity > 0.6 且 acoustic_score > 0.4 → +0.1
    """
    score = 0.0

    # 规则 1：后半段加分
    if video_duration > 0 and evt["peak_time"] > video_duration * 0.8:
        score += 0.15

    # 规则 2：日常寒暄扣分
    seg_duration = evt.get("end_time", 0) - evt.get("start_time", 0)
    if seg_duration < 1.5 and acoustic_score < 0.2:
        score -= 0.2

    # 规则 3：情绪×声学共振加分
    if emotion_intensity > 0.6 and acoustic_score > 0.4:
        score += 0.1

    return max(0.0, min(1.0, score))


def _find_nearest_score(time: float, items: list[dict], time_key: str = "time",
                        score_key: str = "intensity", tolerance: float = 5.0) -> tuple[float, Optional[str], float]:
    """在时间线中找最近的条目（二分查找，O(log n)）。

    Returns:
        (score, label, intensity) 三元组。±tolerance 秒内无匹配返回 (0.0, None, 0.0)。
    """
    if not items:
        return 0.0, None, 0.0
    times = [item[time_key] for item in items]
    idx = bisect.bisect_left(times, time)
    best_score = 0.0
    best_label = None
    best_intensity = 0.0
    best_dist = float("inf")
    for i in range(max(0, idx - 1), min(len(items), idx + 2)):
        dist = abs(items[i][time_key] - time)
        if dist <= tolerance and dist < best_dist:
            best_dist = dist
            best_score = items[i][score_key]
            best_label = items[i].get("label")
            best_intensity = items[i].get("intensity", items[i].get("confidence", 0.0))
    return best_score, best_label, best_intensity


def _find_nearest_transcript(time: float, transcripts: list[dict], tolerance: float) -> str:
    """从 ASR 转写列表中找最近的台词文本（二分查找）。"""
    sorted_t = sorted(transcripts, key=lambda t: t.get("time", 0))
    times = [t.get("time", 0) for t in sorted_t]
    idx = bisect.bisect_left(times, time)
    best_text = ""
    best_dist = float("inf")
    for i in range(max(0, idx - 1), min(len(sorted_t), idx + 2)):
        dist = abs(times[i] - time)
        if dist <= tolerance and dist < best_dist:
            best_dist = dist
            best_text = sorted_t[i].get("text", "").replace(" ", "")
    return best_text


def _find_nearest_keywords(time: float, keyword_events: list[dict],
                           tolerance: float = 5.0) -> tuple[float, list[str], str]:
    """在关键词事件中找最近的匹配（二分查找，O(n log n) 排序 + O(log n) 查找）。
    返回 (score, matched_keywords, subtitle_context)。
    """
    if not keyword_events:
        return 0.0, [], ""

    # 先提取 all_transcripts（如果有）
    all_transcripts = []
    for evt in keyword_events:
        if evt.get("keyword") == "__all_transcripts__":
            all_transcripts = evt.get("all_transcripts", [])
            break

    # 过滤非特殊事件并按时间排序，用于二分查找
    events_sorted = sorted(
        (evt for evt in keyword_events if evt.get("keyword") != "__all_transcripts__"),
        key=lambda e: e["time"]
    )
    if not events_sorted:
        # 无关键词事件，仅尝试 all_transcripts
        best_text = ""
        if all_transcripts:
            best_text = _find_nearest_transcript(time, all_transcripts, 10.0)
        return 0.0, [], best_text

    times = [evt["time"] for evt in events_sorted]
    idx = bisect.bisect_left(times, time)

    best_score = 0.0
    best_keywords: list[str] = []
    best_text = ""
    best_dist = float("inf")
    for i in range(max(0, idx - 1), min(len(events_sorted), idx + 2)):
        dist = abs(times[i] - time)
        if dist <= tolerance and dist < best_dist:
            best_dist = dist
            evt = events_sorted[i]
            best_score = evt["weight"]
            best_keywords = evt.get("matched_keywords", [evt.get("keyword", "")])
            best_text = evt.get("text", "")

    # 没有关键词命中时，从 all_transcripts 找最近台词（10s 窗口）
    if not best_text and all_transcripts:
        best_text = _find_nearest_transcript(time, all_transcripts, 10.0)

    return best_score, best_keywords, best_text


def fuse_and_format_highlights_v2(
    acoustic_events: list[dict],
    video_duration: float,
    max_highlights: int = 2,
    emotion_timeline: Optional[list[dict]] = None,
    keyword_events: Optional[list[dict]] = None,
    emotion_available: bool = False,
    asr_available: bool = False,
    pacing_scores: Optional[list[float]] = None,
) -> list[dict]:
    """V2 融合打分：声学突变 + 情绪 + 剧情节奏 + 关键词 + 规则，自适应权重。

    权重分配（基础）：
        音频核心 70% = (声学 60% + 情绪 30% + 节奏 10%) × 0.70
        关键词 20%
        规则 10%

    自适应降级（当某模块不可用时，权重按比例回退到声学）：
        - 情绪不可用: 42% 声学 + 7% 节奏 + 20% 关键词 + 10% 规则 + 21% 回退声学
        - 节奏不可用: 49% 声学 + 21% 情绪 + 20% 关键词 + 10% 规则
        - ASR 不可用: 42% 声学 + 21% 情绪 + 7% 节奏 + 10% 规则 + 20% 回退声学
        - 全不可用: 70% 声学 + 10% 规则 + 20% 回退声学

    一票否决保留：声学突变分 < 阈值时，总分强制封顶 1 分。

    Args:
        acoustic_events: event_cluster 输出的候选事件列表。
        video_duration: 视频总时长（秒）。
        max_highlights: 最多保留的高光点数，默认 2。
        emotion_timeline: 情绪推理输出的 timeline。
        keyword_events: ASR 关键词匹配事件列表。
        emotion_available: 情绪模型是否可用。
        asr_available: ASR 模型是否可用。
        pacing_scores: 剧情节奏分列表（与 acoustic_events 等长），None 表示不可用。

    Returns:
        ServerHighlightPoint 格式的高光点列表（含可选情绪/关键词字段）。
    """
    if not acoustic_events:
        logger.info("无候选事件，返回空结果")
        return []

    # 动态权重计算
    has_emotion = emotion_available and emotion_timeline
    has_keyword = asr_available and keyword_events
    has_pacing = pacing_scores is not None and any(s > 0 for s in pacing_scores)

    w_acoustic_audio = 0.70 * 0.60   # 0.42
    w_emotion_audio = 0.70 * 0.30    # 0.21
    w_pacing_audio = 0.70 * 0.10     # 0.07
    w_keyword = 0.20
    w_rule = 0.10

    # 不可用模块的权重回退到声学
    fallback = 0.0
    if not has_emotion:
        fallback += w_emotion_audio
        w_emotion_audio = 0.0
    if not has_pacing:
        fallback += w_pacing_audio
        w_pacing_audio = 0.0
    if not has_keyword:
        fallback += w_keyword
        w_keyword = 0.0
    w_acoustic_audio += fallback

    logger.info(f"V2 权重分配: 声学={w_acoustic_audio:.2f}, 情绪={w_emotion_audio:.2f}, "
                f"节奏={w_pacing_audio:.2f}, 关键词={w_keyword:.2f}, 规则={w_rule:.2f} "
                f"(情绪={'✓' if has_emotion else '✗'}, 节奏={'✓' if has_pacing else '✗'}, "
                f"ASR={'✓' if has_keyword else '✗'})")

    # 1. 融合打分 + 分级 + 过滤
    candidates = []
    for i, evt in enumerate(acoustic_events):
        acoustic_score = evt["acoustic_score"]
        peak_time = evt["peak_time"]

        # 一票否决：声学突变分 < 阈值时，总分强制封顶 1 分
        veto = acoustic_score < _VETO_THRESHOLD

        # 情绪分 + 标签（一次二分查找获取三元组）
        emotion_score = 0.0
        emotion_label = None
        emotion_intensity = 0.0
        if has_emotion:
            emotion_score, emotion_label, emotion_intensity = _find_nearest_score(
                peak_time, emotion_timeline, "time", "intensity", tolerance=5.0
            )

        # 节奏分
        pacing_score = 0.0
        if has_pacing and i < len(pacing_scores):
            pacing_score = pacing_scores[i]

        # 关键词分（时间最近匹配）
        kw_score = 0.0
        matched_kw = []
        subtitle_ctx = ""
        if has_keyword:
            kw_score, matched_kw, subtitle_ctx = _find_nearest_keywords(
                peak_time, keyword_events, tolerance=5.0
            )

        # 规则分（D13 扩充：后半段加分/寒暄扣分/情绪共振加分）
        rule_score = _compute_rule_score(evt, video_duration, emotion_score, acoustic_score)

        # 加权融合
        raw_score = (
            acoustic_score * w_acoustic_audio
            + emotion_score * w_emotion_audio
            + pacing_score * w_pacing_audio
            + kw_score * w_keyword
            + rule_score * w_rule
        )

        # 映射到业务分数（一票否决时封顶 1 分）
        score = _map_score(raw_score)
        if veto:
            score = 1.0
        level = _classify(score)
        if level is None:
            continue

        candidates.append({
            **evt,
            "raw_score": round(raw_score, 4),
            "score": score,
            "type": level,
            "emotion_label": emotion_label,
            "emotion_intensity": round(emotion_intensity, 4),
            "matched_keywords": matched_kw,
            "subtitle_context": subtitle_ctx,
        })

    if not candidates:
        logger.info("所有候选事件均低于阈值，返回空结果")
        return []

    # 2. 数量截断 + 区间宽度
    candidates.sort(key=lambda x: x["raw_score"], reverse=True)
    candidates = candidates[:max_highlights]

    # 3. 构建最终输出
    highlights = []
    for evt in candidates:
        start_time, end_time = _enforce_interval_width(
            evt["start_time"], evt["end_time"], evt["peak_time"], video_duration
        )
        hl = {
            "timestamp": evt["peak_time"],
            "start_time": start_time,
            "end_time": end_time,
            "type": evt["type"],
            "confidence": evt["acoustic_score"],
            "score": evt["score"],
            "subtitle_context": evt.get("subtitle_context", ""),
            "comments": [],
            "ai_source": "audio_multimodal" if (has_emotion or has_keyword) else "audio_acoustic",
        }
        # 附加可选字段（客户端不识别则忽略）
        if evt.get("emotion_label"):
            hl["emotion_label"] = evt["emotion_label"]
            hl["emotion_intensity"] = evt["emotion_intensity"]
        if evt.get("matched_keywords"):
            hl["matched_keywords"] = evt["matched_keywords"]

        highlights.append(hl)

    highlights.sort(key=lambda x: x["timestamp"])

    # 4. 间隔过滤：相邻高光点间隔 >= 剧集时长 × 20%
    highlights = _filter_by_spacing(highlights, video_duration)

    # 5. 边界过滤：排除前/后 5% 的高光点
    highlights = _filter_by_boundary(highlights, video_duration)

    logger.info(f"V2 最终输出 {len(highlights)} 个高光点:")
    for i, h in enumerate(highlights):
        extra = ""
        if h.get("emotion_label"):
            extra += f", emotion={h['emotion_label']}({h['emotion_intensity']:.2f})"
        if h.get("matched_keywords"):
            extra += f", kw={h['matched_keywords']}"
        logger.info(f"  高光[{i}]: t={h['timestamp']:.1f}s, score={h['score']}, "
                     f"type={h['type']}, range=[{h['start_time']:.1f}, {h['end_time']:.1f}]{extra}")

    return highlights
