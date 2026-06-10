"""ASR 转写与高光关键词匹配（D8）

依赖：
- model_loader：Paraformer-Tiny ASR 模型、Silero-VAD
- audio_preprocess：输出的 16kHz 单声道 WAV

核心逻辑：
1. 仅对高能量人声段（RMS > 全局均值）进行 ASR 推理，控制耗时
2. 三档高光关键词库（Tier1: 1.0, Tier2: 0.7, Tier3: 0.4）
3. 防误触发：连续 2 字以上命中才算，±2s 内多词命中取最高分不累加
4. ASR 不可用时优雅降级返回空列表
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# ── 三档高光关键词库 ──────────────────────────────────────────

# Tier1 (权重 1.0)：强高光信号词
_KEYWORD_TIER1 = {
    "离婚", "分手", "出轨", "背叛", "怀孕", "流产", "死", "杀了",
    "救命", "不要", "滚", "贱人", "混蛋", "骗子", "疯了",
    "求婚", "结婚", "表白", "喜欢你", "爱你", "在一起",
    "反转", "真相", "秘密", "身世", "亲生", "不是",
    "断绝", "离开", "走", "再也不见",
    "中毒", "受伤", "住院", "昏迷", "抢救",
    "破产", "欠债", "公司", "倒闭",
    "绑架", "威胁", "勒索", "敲诈",
}

# Tier2 (权重 0.7)：中等高光信号词
_KEYWORD_TIER2 = {
    "吵架", "争执", "生气", "愤怒", "委屈", "心痛", "难过",
    "震惊", "不敢相信", "怎么可能", "天哪", "我的天",
    "虐心", "心疼", "可怜", "同情",
    "逆袭", "反击", "报复", "复仇",
    "误会", "解释", "听我说", "你听我",
    "后悔", "对不起", "道歉", "原谅",
    "阴谋", "算计", "设计", "陷害",
    "下药", "偷", "抢", "霸占",
    "告白", "答应", "拒绝", "等你",
}

# Tier3 (权重 0.4)：弱高光信号词
_KEYWORD_TIER3 = {
    "吃瓜", "八卦", "爆料", "曝光",
    "厉害", "牛", "绝了", "离谱",
    "感动", "泪目", "哭了", "破防",
    "甜蜜", "甜", "齁", "好甜",
    "紧张", "刺激", "悬念", "期待",
    "帅", "美", "好看", "绝美",
    "搞笑", "笑死", "哈哈哈", "逗",
    "突然", "忽然", "没想到", "居然",
}

# 构建查找表：keyword -> (tier, weight)
_KEYWORD_MAP: dict[str, tuple[str, float]] = {}
for _kw in _KEYWORD_TIER1:
    _KEYWORD_MAP[_kw] = ("T1", 1.0)
for _kw in _KEYWORD_TIER2:
    _KEYWORD_MAP[_kw] = ("T2", 0.7)
for _kw in _KEYWORD_TIER3:
    _KEYWORD_MAP[_kw] = ("T3", 0.4)

# 防误触发：短于此长度的匹配忽略
_MIN_MATCH_LEN = 2

# 合并窗口：±2s 内的匹配取最高分
_MERGE_WINDOW_SEC = 2.0


def _compute_segment_rms(audio: np.ndarray) -> float:
    """计算音频段 RMS 能量。"""
    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))


def _compute_global_rms(audio: np.ndarray, frame_size: int = 3200) -> float:
    """计算全局 RMS 均值（200ms 帧）。"""
    n_frames = len(audio) // frame_size
    if n_frames == 0:
        return _compute_segment_rms(audio)
    frames = audio[:n_frames * frame_size].reshape(n_frames, frame_size)
    rms_per_frame = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return float(np.mean(rms_per_frame))


def _filter_high_energy_segments(
    audio: np.ndarray,
    speech_segments: list[dict],
) -> list[dict]:
    """筛选高能量人声段（RMS > 全局均值）。

    Args:
        audio: 完整音频 float32 数组。
        speech_segments: VAD 输出的人声段列表。

    Returns:
        高能量人声段列表，每项增加 rms 字段。
    """
    if len(audio) == 0 or not speech_segments:
        return []

    global_rms = _compute_global_rms(audio)
    # 设置最低阈值，避免静音段被误判
    threshold = max(global_rms, 0.005)

    high_energy = []
    for seg in speech_segments:
        start_sample = int(seg["start"] * SAMPLE_RATE)
        end_sample = int(seg["end"] * SAMPLE_RATE)
        start_sample = max(0, start_sample)
        end_sample = min(len(audio), end_sample)

        if start_sample >= end_sample:
            continue

        seg_audio = audio[start_sample:end_sample]
        rms = _compute_segment_rms(seg_audio)

        if rms > threshold:
            high_energy.append({**seg, "rms": round(rms, 6)})

    # 如果没有高能量段，降低阈值重试（处理低音量音频）
    if not high_energy and speech_segments:
        fallback_threshold = max(global_rms * 0.5, 0.001)
        logger.info(
            f"无高能量段，降低阈值重试: {threshold:.4f} → {fallback_threshold:.4f}"
        )
        for seg in speech_segments:
            start_sample = int(seg["start"] * SAMPLE_RATE)
            end_sample = int(seg["end"] * SAMPLE_RATE)
            start_sample = max(0, start_sample)
            end_sample = min(len(audio), end_sample)

            if start_sample >= end_sample:
                continue

            seg_audio = audio[start_sample:end_sample]
            rms = _compute_segment_rms(seg_audio)

            if rms > fallback_threshold:
                high_energy.append({**seg, "rms": round(rms, 6)})

        if high_energy:
            logger.info(f"降级阈值后筛选到 {len(high_energy)} 段")

    logger.info(
        f"高能量段筛选: {len(speech_segments)} 段 → {len(high_energy)} 段 "
        f"(全局RMS阈值={threshold:.4f})"
    )
    return high_energy


def _match_keywords(text: str) -> list[dict]:
    """从 ASR 文本中匹配高光关键词。

    防误触发规则：
    - 连续 2 字以上命中才算有效
    - Tier1 短路：命中即返回最高权重（Early Stop）
    - 返回所有命中的关键词及其权重

    Args:
        text: ASR 转写文本。

    Returns:
        命中关键词列表 [{keyword, tier, weight}]。
    """
    if not text:
        return []

    # Tier1 短路：收集所有命中的 Tier1 关键词
    tier1_matches = []
    for kw in _KEYWORD_TIER1:
        if len(kw) >= _MIN_MATCH_LEN and kw in text:
            tier1_matches.append({"keyword": kw, "tier": "T1", "weight": 1.0})
    if tier1_matches:
        return tier1_matches

    # Tier2 + Tier3 完整搜索
    matches = []
    for keyword, (tier, weight) in _KEYWORD_MAP.items():
        if keyword in _KEYWORD_TIER1:
            continue  # 已在上面处理
        if len(keyword) < _MIN_MATCH_LEN:
            continue
        if keyword in text:
            matches.append({
                "keyword": keyword,
                "tier": tier,
                "weight": weight,
            })

    # 按权重降序排列
    matches.sort(key=lambda x: x["weight"], reverse=True)
    return matches


def _merge_keyword_scores(events: list[dict]) -> list[dict]:
    """合并 ±2s 窗口内的关键词匹配，取最高分不累加。

    Args:
        events: 原始匹配事件 [{time, keyword, tier, weight}]。

    Returns:
        合并后的事件列表 [{time, keyword, tier, weight, matched_keywords}]。
    """
    if not events:
        return []

    # 按时间排序
    events.sort(key=lambda x: x["time"])

    merged = []
    used = set()

    for i, evt in enumerate(events):
        if i in used:
            continue

        # 收集窗口内的所有事件
        group = [evt]
        for j in range(i + 1, len(events)):
            if j in used:
                continue
            if events[j]["time"] - evt["time"] <= _MERGE_WINDOW_SEC:
                group.append(events[j])
                used.add(j)
            else:
                break

        # 取最高权重
        best = max(group, key=lambda x: x["weight"])
        all_keywords = list({g["keyword"] for g in group})

        merged.append({
            "time": best["time"],
            "keyword": best["keyword"],
            "tier": best["tier"],
            "weight": best["weight"],
            "matched_keywords": all_keywords,
        })
        used.add(i)

    return merged


def _run_asr_on_segment(
    asr_model: Any,
    audio_segment: np.ndarray,
    segment_start: float,
) -> list[dict]:
    """对单个人声段运行 ASR 推理。

    Args:
        asr_model: funasr AutoModel 实例。
        audio_segment: 音频段 float32 数组。
        segment_start: 该段在完整音频中的起始时间（秒）。

    Returns:
        识别结果列表 [{text, start, end}]，时间为全局时间戳。
    """
    try:
        # funasr 接受 numpy array
        result = asr_model.generate(
            input=audio_segment,
            batch_size_s=300,
        )

        if not result:
            return []

        segments = []
        for item in result:
            text = item.get("text", "").strip()
            if not text:
                continue

            # funasr Paraformer 返回 timestamp 字段
            # 格式可能是 [[start_ms, end_ms], ...] 或 {"start": ..., "end": ...}
            timestamps = item.get("timestamp", [])
            if timestamps and isinstance(timestamps[0], list):
                # 字级时间戳：[[start_ms, end_ms], ...]
                # 取整体区间
                start_ms = timestamps[0][0] if timestamps else 0
                end_ms = timestamps[-1][1] if timestamps else 0
                seg_start = segment_start + start_ms / 1000.0
                seg_end = segment_start + end_ms / 1000.0
            elif isinstance(timestamps, dict):
                seg_start = segment_start + timestamps.get("start", 0) / 1000.0
                seg_end = segment_start + timestamps.get("end", 0) / 1000.0
            else:
                # 无时间戳，使用段的起止时间
                duration = len(audio_segment) / SAMPLE_RATE
                seg_start = segment_start
                seg_end = segment_start + duration

            segments.append({
                "text": text,
                "start": round(seg_start, 3),
                "end": round(seg_end, 3),
            })

        return segments

    except Exception as e:
        logger.warning(f"ASR 段推理失败: {e}")
        return []


def transcribe_and_match(
    audio_path: str,
    speech_segments: list[dict],
    cancel_event: Optional[threading.Event] = None,
) -> list[dict]:
    """ASR 转写 + 高光关键词匹配（主入口）。

    流程：
    1. 加载音频
    2. 筛选高能量人声段（RMS > 全局均值）
    3. 对高能量段逐段 ASR 推理
    4. 关键词匹配 + ±2s 合并

    Args:
        audio_path: 16kHz 单声道 WAV 文件路径。
        speech_segments: VAD 输出的人声段列表。

    Returns:
        关键词命中事件列表 [{time, keyword, tier, weight, matched_keywords, text}]。
        ASR 不可用或无命中返回空列表。
    """
    # 检查 ASR 是否可用
    from model_loader import get_manager
    manager = get_manager()
    if not manager.is_asr_available():
        logger.info("ASR 模型不可用，跳过关键词匹配")
        return []

    asr_model = manager.get_asr_model()
    if asr_model is None:
        logger.info("ASR 模型未加载，跳过关键词匹配")
        return []

    # 加载音频
    try:
        import soundfile as sf
        audio, sr = sf.read(audio_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            # 简单重采样
            duration = len(audio) / sr
            target_len = int(duration * SAMPLE_RATE)
            indices = np.linspace(0, len(audio) - 1, target_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
    except Exception as e:
        logger.error(f"音频加载失败: {e}")
        return []

    # 直接使用所有人声段（VAD 已筛选），不做二次能量过滤
    if not speech_segments:
        logger.info("无人声段，跳过 ASR")
        return []

    # 逐段 ASR 推理
    _EARLY_STOP_TIER1_COUNT = 2
    all_keyword_events = []
    all_transcripts = []  # 保留所有 ASR 文本，供 highlight_context 使用
    for seg in speech_segments:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("ASR 推理被取消（cooperative）")
            break
        if seg["end"] - seg["start"] < 0.5:
            continue

        tier1_count = sum(1 for e in all_keyword_events if e.get("tier") == "T1")
        if tier1_count >= _EARLY_STOP_TIER1_COUNT:
            logger.info(f"已累计 {tier1_count} 个 Tier1 命中，跳过剩余段")
            break

        start_sample = int(seg["start"] * SAMPLE_RATE)
        end_sample = int(seg["end"] * SAMPLE_RATE)
        start_sample = max(0, start_sample)
        end_sample = min(len(audio), end_sample)

        if start_sample >= end_sample:
            continue

        seg_audio = audio[start_sample:end_sample]
        asr_results = _run_asr_on_segment(asr_model, seg_audio, seg["start"])

        # 保留所有 ASR 转写文本
        for asr_seg in asr_results:
            text = asr_seg.get("text", "").strip()
            if text:
                mid_time = (asr_seg["start"] + asr_seg["end"]) / 2
                all_transcripts.append({"time": round(mid_time, 1), "text": text})

        # 对每段 ASR 结果做关键词匹配
        for asr_seg in asr_results:
            matches = _match_keywords(asr_seg["text"])
            if not matches:
                continue

            # 每个匹配生成一个事件
            mid_time = (asr_seg["start"] + asr_seg["end"]) / 2
            for m in matches:
                all_keyword_events.append({
                    "time": mid_time,
                    "keyword": m["keyword"],
                    "tier": m["tier"],
                    "weight": m["weight"],
                    "text": asr_seg["text"],
                })

    # 无论是否有关键词命中，都返回结果（包含所有 ASR 文本）
    if not all_keyword_events and not all_transcripts:
        logger.info("ASR 转写完成，无识别结果")
        return []

    # ±2s 合并（仅在有关键词命中时）
    merged = _merge_keyword_scores(all_keyword_events) if all_keyword_events else []

    # 附加 text 信息
    for evt in merged:
        # 找到该事件对应的原始 text
        nearby_texts = [
            e["text"] for e in all_keyword_events
            if abs(e["time"] - evt["time"]) <= _MERGE_WINDOW_SEC
        ]
        evt["text"] = nearby_texts[0] if nearby_texts else ""

    # 添加包含所有 ASR 文本的特殊事件（供 highlight_context 使用）
    if all_transcripts:
        merged.append({
            "time": -1,
            "keyword": "__all_transcripts__",
            "tier": "NONE",
            "weight": 0,
            "text": "",
            "all_transcripts": all_transcripts,
        })

    logger.info(f"关键词匹配完成: {len(merged)} 个命中事件, {len(all_transcripts)} 条 ASR 文本")
    for evt in merged:
        if evt.get("keyword") == "__all_transcripts__":
            logger.info(f"  all_transcripts: {len(evt['all_transcripts'])} snippets")
        else:
            logger.info(
                f"  t={evt['time']:.1f}s, keyword={evt['keyword']}, "
                f"tier={evt['tier']}, weight={evt['weight']}, "
                f"text=\"{evt['text'][:50]}\""
            )

    return merged