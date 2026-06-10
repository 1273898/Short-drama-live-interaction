"""高光上下文对象：聚合 ASR 台词、情绪推理、关键词匹配结果

用于 roast_generator 的 LLM prompt 构建和本地模板填充。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HighlightContext:
    """高光点上下文，聚合多模态推理结果"""
    timestamp: float = 0.0
    dialogue_snippets: list = field(default_factory=list)   # [{time, text}]
    dominant_emotion: str = "平淡"
    emotion_confidence: float = 0.0
    matched_keywords: list = field(default_factory=list)    # 命中关键词文本
    keyword_tier: int = 3                                   # 1/2/3
    acoustic_score: float = 0.0
    highlight_level: str = "L2"                             # L1 或 L2

    @property
    def has_dialogue(self) -> bool:
        return bool(self.dialogue_snippets)

    @property
    def dialogue_text(self) -> str:
        """合并台词片段为一行（用于 prompt）"""
        parts = [s["text"] for s in self.dialogue_snippets if s.get("text")]
        return "；".join(parts)


def build_highlight_context(
    highlight: dict,
    emotion_result: Optional[dict] = None,
    keyword_events: Optional[list] = None,
    window_sec: float = 15.0,
) -> HighlightContext:
    """从管线中间结果构建高光上下文。

    Args:
        highlight: score_fusion 输出的高光点 dict。
        emotion_result: torch_emotion_infer 输出（含 timeline）。
        keyword_events: torch_asr_infer 输出的关键词事件列表。
        window_sec: 台词提取窗口（秒），默认 15s。
    """
    # 高光时刻的台词经常略晚于 peak_time（ASR 时间戳偏移），向前扩展 3s 缓冲
    forward_buffer = 3.0
    timestamp = highlight.get("timestamp", 0.0)
    hl_type = highlight.get("type", "L2")
    acoustic_score = highlight.get("confidence", 0.0)

    # ── 1. 提取上文台词片段（[timestamp - window_sec, timestamp]，去重保序） ──
    snippets = []

    # 优先从 all_transcripts 提取（ASR 完整转写，包含未命中关键词的文本）
    all_transcripts_event = None
    if keyword_events:
        for evt in keyword_events:
            if evt.get("keyword") == "__all_transcripts__":
                all_transcripts_event = evt
                break

    if all_transcripts_event and all_transcripts_event.get("all_transcripts"):
        seen = set()
        for t in all_transcripts_event["all_transcripts"]:
            t_time = t.get("time", 0)
            t_text = t.get("text", "").strip().replace(" ", "")  # 去除 ASR 空格
            if t_text and t_text not in seen and timestamp - window_sec <= t_time <= timestamp + forward_buffer:
                seen.add(t_text)
                snippets.append({"time": t_time, "text": t_text})
        snippets.sort(key=lambda x: x["time"])

    # Fallback：all_transcripts 无结果时，从 keyword_events 的 text 字段提取
    if not snippets and keyword_events:
        seen = set()
        for evt in keyword_events:
            if evt.get("keyword") == "__all_transcripts__":
                continue
            if timestamp - window_sec <= evt["time"] <= timestamp + forward_buffer:
                text = evt.get("text", "").strip()
                if text and text not in seen:
                    seen.add(text)
                    snippets.append({"time": round(evt["time"], 1), "text": text})
        snippets.sort(key=lambda x: x["time"])

    # Fallback：使用 highlight 自带的 subtitle_context
    if not snippets:
        subtitle_ctx = highlight.get("subtitle_context", "").strip()
        if subtitle_ctx:
            snippets.append({"time": round(timestamp, 1), "text": subtitle_ctx})

    # ── 2. 主导情绪（优先用 highlight 附加字段，再从 timeline 匹配） ──
    dominant_emotion = highlight.get("emotion_label", "")
    emotion_confidence = highlight.get("emotion_intensity", 0.0)

    if emotion_result and emotion_result.get("available"):
        timeline = emotion_result.get("timeline", [])
        best_dist = float("inf")
        for entry in timeline:
            if entry["time"] > timestamp + forward_buffer:
                continue
            dist = abs(timestamp - entry["time"])
            if dist <= 5.0 and dist < best_dist:
                best_dist = dist
                dominant_emotion = entry["label"]
                emotion_confidence = entry.get("confidence", entry.get("intensity", 0.0))

    if not dominant_emotion:
        dominant_emotion = "平淡"

    # ── 3. 命中关键词 + tier ──
    matched_kw = list(highlight.get("matched_keywords", []))
    keyword_tier = 3

    if keyword_events:
        best_dist = float("inf")
        for evt in keyword_events:
            if evt.get("keyword") == "__all_transcripts__":
                continue
            if evt["time"] > timestamp + forward_buffer:
                continue
            dist = abs(timestamp - evt["time"])
            if dist <= 5.0 and dist < best_dist:
                best_dist = dist
                kws = evt.get("matched_keywords", [])
                if not kws:
                    kw = evt.get("keyword", "")
                    if kw:
                        kws = [kw]
                if kws:
                    matched_kw = kws
                    tier_str = evt.get("tier", "T3")
                    keyword_tier = int(tier_str.replace("T", "")) if isinstance(tier_str, str) else tier_str

    ctx = HighlightContext(
        timestamp=timestamp,
        dialogue_snippets=snippets,
        dominant_emotion=dominant_emotion,
        emotion_confidence=round(emotion_confidence, 4),
        matched_keywords=matched_kw,
        keyword_tier=keyword_tier,
        acoustic_score=acoustic_score,
        highlight_level=hl_type,
    )

    logger.info(
        f"HighlightContext: t={timestamp:.1f}s, emotion={dominant_emotion}({emotion_confidence:.2f}), "
        f"kw={matched_kw}, tier=T{keyword_tier}, dialogue={len(snippets)} snippets"
    )
    return ctx
