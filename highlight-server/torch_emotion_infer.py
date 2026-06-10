"""人声情绪推理

依赖：
- model_loader：emotion2vec 情绪模型（FunASR）
- audio_preprocess：输出的 16kHz 单声道 WAV

核心逻辑：
1. 对 VAD 人声段做 emotion2vec 情绪推理（8→4分类映射：喜/怒/悲/平淡）
2. 超长音频(>10s)分片推理，避免 OOM
3. 情绪强度 = (1 - 平淡概率) × 情绪类别权重
4. 模型不可用时优雅降级返回零值
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# 最大片段时长（秒），超过则分片
_MAX_SEGMENT_SEC = 10.0

# 情绪类别权重（愤怒/悲伤权重最高，因为短剧中更可能是高光）
_EMOTION_WEIGHTS = {
    "喜": 0.7,
    "怒": 1.0,
    "悲": 0.9,
    "平淡": 0.0,
}

# 撒娇/甜蜜词典（用于防误判：怒→喜 降级）
_COQUETTISH_WORDS = {
    "讨厌", "讨厌啦", "哼", "不理你", "才不要", "臭不要脸",
    "坏蛋", "大坏蛋", "你坏", "欺负我", "人家", "人家不要",
    "撒娇", "哄我", "抱抱", "亲亲", "想你", "爱死你",
    "小笨蛋", "傻瓜", "笨死了", "你好坏", "坏人",
}

# 甜蜜/撒糖词典（用于防误判：悲→喜 降级）
_SWEET_WORDS = {
    "爱你", "喜欢你", "在一起", "好甜", "甜蜜", "齁",
    "心动", "脸红", "害羞", "表白", "在一起", "幸福",
    "浪漫", "心动", "小鹿乱撞", "好暖", "好宠",
}

# 悲伤/催泪词典（用于防误判：喜→悲 升级）
_SAD_WORDS = {
    "死", "去世", "离开", "再见", "对不起", "来不及",
    "遗憾", "遗书", "遗言", "下辈子", "来世", "天堂",
    "哭", "泪", "心碎", "心痛", "难过", "伤心",
}

# 高光关键词 Tier1（用于平淡→怒 升级）
_TIER1_KEYWORDS = {
    "离婚", "分手", "出轨", "背叛", "怀孕", "流产", "死", "杀了",
    "救命", "不要", "滚", "贱人", "混蛋", "骗子", "疯了",
    "反转", "真相", "秘密", "身世", "亲生",
    "破产", "欠债", "绑架", "威胁", "勒索",
}


# 模型输出标签 → 标准化标签映射（支持 emotion2vec 8-class 格式）
_LABEL_MAP = {
    # emotion2vec 格式（中文/英文）
    "生气/angry": "怒", "厌恶/disgusted": "怒",
    "开心/happy": "喜", "吃惊/surprised": "喜",
    "难过/sad": "悲", "恐惧/fearful": "悲",
    "中立/neutral": "平淡", "其他/other": "平淡",
    # 纯英文标签
    "angry": "怒", "anger": "怒", "fury": "怒", "disgusted": "怒",
    "happy": "喜", "joy": "喜", "happiness": "喜", "surprised": "喜",
    "sad": "悲", "sadness": "悲", "sorrow": "悲", "fearful": "悲",
    "neutral": "平淡", "calm": "平淡", "other": "平淡",
    # 纯中文标签
    "愤怒": "怒", "生气": "怒", "厌恶": "怒",
    "高兴": "喜", "开心": "喜", "快乐": "喜", "吃惊": "喜",
    "悲伤": "悲", "伤心": "悲", "难过": "悲", "恐惧": "悲",
    "中性": "平淡", "平静": "平淡",
}


def compute_emotion_intensity(
    emotion_probs: dict[str, float],
) -> tuple[float, str]:
    """根据情绪概率分布计算强度和标签。

    公式：intensity = (1 - P(平淡)) × category_weight

    Args:
        emotion_probs: {标签: 概率}，标签需要先经 _LABEL_MAP 标准化。

    Returns:
        (intensity, label) 元组。intensity ∈ [0, 1]。
    """
    if not emotion_probs:
        return 0.0, "平淡"

    # 找到概率最高的情绪
    best_label = max(emotion_probs, key=emotion_probs.get)
    best_prob = emotion_probs[best_label]
    neutral_prob = emotion_probs.get("平淡", 0.0)

    # 强度 = (1 - 平淡概率) × 类别权重
    category_weight = _EMOTION_WEIGHTS.get(best_label, 0.5)
    intensity = (1.0 - neutral_prob) * category_weight

    # 限制在 [0, 1]
    intensity = max(0.0, min(1.0, intensity))

    return round(intensity, 4), best_label


def correct_emotion_with_text(
    emotion_label: str,
    emotion_intensity: float,
    confidence: float,
    segment_start: float,
    segment_end: float,
    keyword_events: list[dict],
) -> tuple[str, float, float]:
    """文本辅助情绪防误判（D10）。

    当情绪模型判断与文本信号矛盾时，纠正情绪标签。

    规则：
    1. 怒 + 撒娇词 → 降级为喜（模型把撒娇语气误判为愤怒）
    2. 悲 + 甜蜜词 → 降级为喜（模型把感动语气误判为悲伤）
    3. 喜 + 悲伤词 → 升级为悲（模型把苦笑/自嘲笑判为开心）
    4. 平淡 + ≥2 个 Tier1 关键词 → 升级为怒（声学平缓但文本高能）

    Args:
        emotion_label: 当前情绪标签。
        emotion_intensity: 当前情绪强度。
        confidence: 当前置信度。
        segment_start: 段起始时间（秒）。
        segment_end: 段结束时间（秒）。
        keyword_events: ASR 关键词事件列表。

    Returns:
        (corrected_label, corrected_intensity, corrected_confidence)
    """
    # 收集该时间窗口内的 ASR 文本
    window_texts = []
    tier1_count = 0
    for evt in keyword_events:
        evt_time = evt.get("time", 0)
        if segment_start - 2.0 <= evt_time <= segment_end + 2.0:
            window_texts.append(evt.get("text", ""))
            if evt.get("tier") == "T1":
                tier1_count += 1

    combined_text = " ".join(window_texts)

    if not combined_text:
        return emotion_label, emotion_intensity, confidence

    # 规则 1：怒 + 撒娇词 → 喜
    if emotion_label == "怒":
        if any(w in combined_text for w in _COQUETTISH_WORDS):
            logger.info(
                f"情绪纠正: 怒→喜 (撒娇词命中), "
                f"t=[{segment_start:.1f},{segment_end:.1f}], text=\"{combined_text[:40]}\""
            )
            new_intensity = max(emotion_intensity * 0.6, 0.3)
            return "喜", round(new_intensity, 4), round(confidence * 0.7, 4)

    # 规则 2：悲 + 甜蜜词 → 喜
    if emotion_label == "悲":
        if any(w in combined_text for w in _SWEET_WORDS):
            logger.info(
                f"情绪纠正: 悲→喜 (甜蜜词命中), "
                f"t=[{segment_start:.1f},{segment_end:.1f}], text=\"{combined_text[:40]}\""
            )
            new_intensity = max(emotion_intensity * 0.7, 0.3)
            return "喜", round(new_intensity, 4), round(confidence * 0.7, 4)

    # 规则 3：喜 + 悲伤词 → 悲
    if emotion_label == "喜":
        sad_hits = sum(1 for w in _SAD_WORDS if w in combined_text)
        if sad_hits >= 2:
            logger.info(
                f"情绪纠正: 喜→悲 (悲伤词×{sad_hits}), "
                f"t=[{segment_start:.1f},{segment_end:.1f}], text=\"{combined_text[:40]}\""
            )
            new_intensity = max(emotion_intensity * 0.8, 0.4)
            return "悲", round(new_intensity, 4), round(confidence * 0.6, 4)

    # 规则 4：平淡 + ≥2 个 Tier1 关键词 → 怒
    if emotion_label == "平淡" and tier1_count >= 2:
        logger.info(
            f"情绪纠正: 平淡→怒 (Tier1关键词×{tier1_count}), "
            f"t=[{segment_start:.1f},{segment_end:.1f}], text=\"{combined_text[:40]}\""
        )
        return "怒", round(min(0.5 + tier1_count * 0.1, 1.0), 4), round(0.5, 4)

    return emotion_label, emotion_intensity, confidence


def apply_text_correction(
    emotion_result: dict,
    keyword_events: list[dict],
) -> dict:
    """对已有情绪推理结果应用文本纠正（轻量级，不重新推理）。

    Args:
        emotion_result: infer_emotion_for_segments 的输出。
        keyword_events: ASR 关键词事件列表。

    Returns:
        纠正后的 emotion_result（原地修改 + 返回）。
    """
    if not emotion_result.get("available") or not keyword_events:
        return emotion_result

    timeline = emotion_result.get("timeline", [])
    if not timeline:
        return emotion_result

    best_intensity = 0.0
    best_label = "平淡"
    best_confidence = 0.0

    for entry in timeline:
        c_label, c_intensity, c_conf = correct_emotion_with_text(
            entry["label"], entry["intensity"], entry["confidence"],
            entry["start"], entry["end"], keyword_events,
        )
        entry["label"] = c_label
        entry["intensity"] = c_intensity
        entry["confidence"] = c_conf
        if c_intensity > best_intensity:
            best_intensity = c_intensity
            best_label = c_label
            best_confidence = c_conf

    emotion_result["intensity"] = best_intensity
    emotion_result["label"] = best_label
    emotion_result["confidence"] = round(best_confidence, 4)

    logger.info(f"文本纠正后: {best_label}({best_intensity:.3f}), {len(timeline)} 段")
    return emotion_result


def _split_audio_chunks(audio: np.ndarray, chunk_samples: int) -> list[np.ndarray]:
    """将音频按固定长度分片。"""
    chunks = []
    for i in range(0, len(audio), chunk_samples):
        chunk = audio[i:i + chunk_samples]
        if len(chunk) > 0:
            chunks.append(chunk)
    return chunks


def _normalize_label(raw_label: str) -> str:
    """将模型输出标签标准化为中文四分类。"""
    lower = raw_label.lower().strip()
    return _LABEL_MAP.get(lower, "平淡")


def infer_emotion_for_segments(
    audio_path: str,
    speech_segments: list[dict],
    keyword_events: Optional[list[dict]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> dict:
    """对 VAD 人声段进行情绪推理（主入口）。

    流程：
    1. 加载音频
    2. 对每个人声段切片，逐段推理
    3. 汇总最强情绪作为整体结果
    4. 同时输出各段时间加权的情绪强度时间线

    Args:
        audio_path: 16kHz 单声道 WAV 文件路径。
        speech_segments: VAD 输出的人声段列表。
        keyword_events: ASR 关键词事件列表（可选，用于文本辅助情绪纠正）。

    Returns:
        {
            "intensity": float,       # 整体情绪强度 [0, 1]
            "label": str,             # 整体情绪标签
            "confidence": float,      # 最高置信度
            "timeline": [             # 各段情绪强度时间线
                {"start": float, "end": float, "intensity": float, "label": str},
            ],
            "available": bool,        # 模型是否可用
        }
    """
    result_default = {
        "intensity": 0.0,
        "label": "平淡",
        "confidence": 0.0,
        "timeline": [],
        "available": False,
    }

    # 检查模型可用性
    from model_loader import get_manager
    manager = get_manager()
    if not manager.is_emotion_available():
        logger.info("情绪模型不可用，返回零值")
        return result_default

    model = manager.get_emotion_model()
    if model is None:
        logger.info("情绪模型未加载，返回零值")
        return result_default

    # 加载音频
    try:
        import soundfile as sf
        audio, sr = sf.read(audio_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            duration = len(audio) / sr
            target_len = int(duration * SAMPLE_RATE)
            indices = np.linspace(0, len(audio) - 1, target_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
    except Exception as e:
        logger.error(f"情绪推理音频加载失败: {e}")
        return result_default

    if not speech_segments:
        return result_default

    # 分片参数
    chunk_samples = int(_MAX_SEGMENT_SEC * SAMPLE_RATE)

    # 合并间隔 <1.0s 的相邻段，减少推理次数
    merged_segments = []
    for seg in speech_segments:
        if merged_segments and seg["start"] - merged_segments[-1]["end"] < 1.0:
            merged_segments[-1]["end"] = seg["end"]
        else:
            merged_segments.append(dict(seg))

    # 逐段推理
    timeline = []
    best_intensity = 0.0
    best_label = "平淡"
    best_confidence = 0.0
    global_rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if len(audio) > 0 else 0.0

    for seg in merged_segments:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("情绪推理被取消（cooperative）")
            break
        start_sample = int(seg["start"] * SAMPLE_RATE)
        end_sample = int(seg["end"] * SAMPLE_RATE)
        start_sample = max(0, start_sample)
        end_sample = min(len(audio), end_sample)

        if start_sample >= end_sample:
            continue

        seg_audio = audio[start_sample:end_sample]
        seg_duration = len(seg_audio) / SAMPLE_RATE

        if seg_duration < 0.3:
            continue

        # Early Exit：已发现强情绪段时，跳过低能量段
        if best_intensity > 0.8:
            seg_rms = float(np.sqrt(np.mean(seg_audio.astype(np.float64) ** 2)))
            if seg_rms < global_rms * 0.5:
                continue

        # 超长段分片推理
        if len(seg_audio) > chunk_samples:
            chunks = _split_audio_chunks(seg_audio, chunk_samples)
        else:
            chunks = [seg_audio]

        # 收集各片概率取均值
        all_probs: dict[str, float] = {}
        chunk_count = 0

        for chunk in chunks:
            try:
                # emotion2vec: model.generate(input=numpy_array, granularity="utterance")
                # 返回 [{"labels": [...], "scores": [...]}]，scores 已 softmax
                res = model.generate(input=chunk, granularity="utterance")
                if not res or not isinstance(res, list):
                    continue

                item = res[0] if isinstance(res[0], dict) else res
                labels_raw = item.get("labels", [])
                scores_raw = item.get("scores", [])

                if not labels_raw or not scores_raw:
                    continue

                # 8→4 class 映射：累加同类别概率
                for raw_label, score in zip(labels_raw, scores_raw):
                    std_label = _normalize_label(raw_label)
                    all_probs[std_label] = all_probs.get(std_label, 0.0) + float(score)

                chunk_count += 1
            except Exception as e:
                logger.warning(f"情绪推理片段失败: {e}")
                continue

        if chunk_count == 0:
            continue

        # 概率取均值
        for label in all_probs:
            all_probs[label] /= chunk_count

        # 计算强度
        intensity, label = compute_emotion_intensity(all_probs)
        confidence = max(all_probs.values()) if all_probs else 0.0

        mid_time = (seg["start"] + seg["end"]) / 2
        timeline.append({
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "time": round(mid_time, 3),
            "intensity": intensity,
            "label": label,
            "confidence": round(confidence, 4),
        })

        # 更新全局最强情绪
        if intensity > best_intensity:
            best_intensity = intensity
            best_label = label
            best_confidence = confidence

    # 文本辅助情绪纠正（D10）
    kw_events = keyword_events or []
    if kw_events and timeline:
        corrected_timeline = []
        best_intensity = 0.0
        best_label = "平淡"
        best_confidence = 0.0
        for entry in timeline:
            c_label, c_intensity, c_conf = correct_emotion_with_text(
                entry["label"], entry["intensity"], entry["confidence"],
                entry["start"], entry["end"], kw_events,
            )
            entry["label"] = c_label
            entry["intensity"] = c_intensity
            entry["confidence"] = c_conf
            corrected_timeline.append(entry)
            if c_intensity > best_intensity:
                best_intensity = c_intensity
                best_label = c_label
                best_confidence = c_conf
        timeline = corrected_timeline

    result = {
        "intensity": best_intensity,
        "label": best_label,
        "confidence": round(best_confidence, 4),
        "timeline": timeline,
        "available": True,
    }

    logger.info(
        f"情绪推理完成: {len(timeline)} 段, "
        f"整体={best_label}({best_intensity:.3f}), "
        f"置信度={best_confidence:.3f}"
    )
    return result
