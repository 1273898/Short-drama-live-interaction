"""highlight_context 构建测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from highlight_context import HighlightContext, build_highlight_context


class TestHighlightContextDataclass:
    """HighlightContext 数据类测试"""

    def test_default_values(self):
        ctx = HighlightContext()
        assert ctx.timestamp == 0.0
        assert ctx.dialogue_snippets == []
        assert ctx.dominant_emotion == "平淡"
        assert ctx.has_dialogue is False

    def test_has_dialogue_true(self):
        ctx = HighlightContext(dialogue_snippets=[{"time": 5.0, "text": "你好"}])
        assert ctx.has_dialogue is True

    def test_dialogue_text_join(self):
        ctx = HighlightContext(
            dialogue_snippets=[
                {"time": 5.0, "text": "你好"},
                {"time": 8.0, "text": "再见"},
            ]
        )
        assert ctx.dialogue_text == "你好；再见"

    def test_dialogue_text_empty(self):
        ctx = HighlightContext()
        assert ctx.dialogue_text == ""


class TestBuildContext:
    """build_highlight_context 测试"""

    def _make_highlight(self, **kwargs):
        hl = {
            "timestamp": 30.0,
            "type": "L1",
            "confidence": 0.7,
            "emotion_label": "怒",
            "emotion_intensity": 0.8,
            "matched_keywords": ["滚出去"],
        }
        hl.update(kwargs)
        return hl

    def test_basic_context(self):
        hl = self._make_highlight()
        ctx = build_highlight_context(hl)
        assert ctx.timestamp == 30.0
        assert ctx.highlight_level == "L1"
        assert ctx.acoustic_score == 0.7
        assert ctx.dominant_emotion == "怒"
        assert ctx.emotion_confidence == 0.8
        assert ctx.matched_keywords == ["滚出去"]

    def test_dialogue_extraction_within_window(self):
        """只提取高光点之前的台词（上文），不取未来内容"""
        hl = self._make_highlight(timestamp=30.0)
        keyword_events = [
            {"time": 25.0, "text": "你给我滚"},
            {"time": 30.0, "text": "我不走"},
            {"time": 35.0, "text": "别拦我"},       # 未来，不取
            {"time": 50.0, "text": "这条太远了"},   # 未来且超出窗口
        ]
        ctx = build_highlight_context(hl, keyword_events=keyword_events, window_sec=10.0)
        assert len(ctx.dialogue_snippets) == 2
        texts = [s["text"] for s in ctx.dialogue_snippets]
        assert "别拦我" not in texts
        assert "这条太远了" not in texts

    def test_dialogue_dedup(self):
        """重复台词应去重"""
        hl = self._make_highlight(timestamp=30.0)
        keyword_events = [
            {"time": 25.0, "text": "你好"},
            {"time": 28.0, "text": "你好"},
            {"time": 30.0, "text": "再见"},
        ]
        ctx = build_highlight_context(hl, keyword_events=keyword_events)
        assert len(ctx.dialogue_snippets) == 2

    def test_no_dialogue_fallback(self):
        """ASR 结果为空 → dialogue_snippets 空列表，不报错"""
        hl = self._make_highlight()
        ctx = build_highlight_context(hl, keyword_events=None)
        assert ctx.dialogue_snippets == []
        assert ctx.has_dialogue is False

    def test_empty_keyword_events(self):
        hl = self._make_highlight()
        ctx = build_highlight_context(hl, keyword_events=[])
        assert ctx.dialogue_snippets == []

    def test_emotion_from_timeline(self):
        """highlight 无 emotion_label 时，从 emotion_result timeline 匹配"""
        hl = self._make_highlight(emotion_label="", emotion_intensity=0.0)
        emotion_result = {
            "available": True,
            "timeline": [
                {"time": 28.0, "label": "悲", "intensity": 0.6, "confidence": 0.65},
                {"time": 35.0, "label": "喜", "intensity": 0.9, "confidence": 0.9},
            ],
        }
        ctx = build_highlight_context(hl, emotion_result=emotion_result)
        assert ctx.dominant_emotion == "悲"
        assert ctx.emotion_confidence == 0.65

    def test_emotion_fallback_to平淡(self):
        """无情绪数据时默认"平淡" """
        hl = self._make_highlight(emotion_label="", emotion_intensity=0.0)
        ctx = build_highlight_context(hl, emotion_result=None)
        assert ctx.dominant_emotion == "平淡"

    def test_keywords_from_events(self):
        """从 keyword_events 匹配最近的关键词和 tier"""
        hl = self._make_highlight(matched_keywords=[])
        keyword_events = [
            {"time": 29.0, "matched_keywords": ["分手"], "tier": "T1"},
        ]
        ctx = build_highlight_context(hl, keyword_events=keyword_events)
        assert ctx.matched_keywords == ["分手"]
        assert ctx.keyword_tier == 1

    def test_dialogue_sorted_by_time(self):
        """台词应按时间排序"""
        hl = self._make_highlight(timestamp=30.0)
        keyword_events = [
            {"time": 32.0, "text": "后"},
            {"time": 28.0, "text": "前"},
            {"time": 30.0, "text": "中"},
        ]
        ctx = build_highlight_context(hl, keyword_events=keyword_events)
        times = [s["time"] for s in ctx.dialogue_snippets]
        assert times == sorted(times)

    def test_emotion_result_not_available(self):
        """emotion_result.available=False 时不从 timeline 匹配"""
        hl = self._make_highlight(emotion_label="", emotion_intensity=0.0)
        emotion_result = {
            "available": False,
            "timeline": [
                {"time": 30.0, "label": "怒", "intensity": 0.9, "confidence": 0.9},
            ],
        }
        ctx = build_highlight_context(hl, emotion_result=emotion_result)
        assert ctx.dominant_emotion == "平淡"
