"""roast_generator 上下文感知 + LLM 校验测试"""
import asyncio
import pytest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from highlight_context import HighlightContext
from roast_generator import (
    _build_llm_prompt,
    _parse_comments,
    _validate_comments,
    score_comment,
    is_similar_to_any,
    generate_comments_for_highlight,
)


class TestBuildLlmPrompt:
    """LLM prompt 构建测试"""

    def _make_ctx(self, **kwargs):
        defaults = {
            "timestamp": 30.0,
            "highlight_level": "L1",
            "dominant_emotion": "怒",
            "emotion_confidence": 0.8,
            "matched_keywords": ["滚出去"],
            "dialogue_snippets": [
                {"time": 28.0, "text": "你给我滚"},
                {"time": 30.0, "text": "我不走"},
            ],
        }
        defaults.update(kwargs)
        return HighlightContext(**defaults)

    def test_prompt_contains_dialogue(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3)
        assert "你给我滚" in prompt
        assert "我不走" in prompt

    def test_prompt_contains_emotion(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3)
        assert "怒" in prompt

    def test_prompt_contains_keywords(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3)
        assert "滚出去" in prompt

    def test_prompt_contains_no_hallucinate_constraint(self):
        """prompt 应包含禁止编造人名的约束"""
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3)
        assert "禁止编造" in prompt or "禁止" in prompt

    def test_prompt_no_dialogue_fallback(self):
        """无台词时 prompt 应显示"无台词数据" """
        ctx = self._make_ctx(dialogue_snippets=[])
        prompt = _build_llm_prompt(ctx, count=3)
        assert "无台词数据" in prompt

    def test_prompt_contains_drama_info(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3, drama_title="测试剧", episode_title="第1集")
        assert "测试剧" in prompt
        assert "第1集" in prompt

    def test_prompt_contains_highlight_level(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=3)
        assert "L1" in prompt

    def test_prompt_contains_count(self):
        ctx = self._make_ctx()
        prompt = _build_llm_prompt(ctx, count=5)
        assert "5" in prompt


class TestParseComments:
    """LLM 返回解析测试"""

    def test_parse_json_array(self):
        text = '[{"emotionTag": "震惊", "text": "这也太狠了吧"}]'
        result = _parse_comments(text)
        assert len(result) == 1
        assert result[0]["emotionTag"] == "震惊"
        assert result[0]["text"] == "这也太狠了吧"

    def test_parse_tag_text_format(self):
        text = "[震惊]这也太狠了吧\n[愤怒]简直气死了"
        result = _parse_comments(text)
        assert len(result) == 2
        assert result[0]["emotionTag"] == "震惊"
        assert result[1]["emotionTag"] == "愤怒"

    def test_parse_nested_tags(self):
        """'[吐槽] - [震惊]xxx' → 提取最后一个 tag"""
        text = "[吐槽] - [震惊]这也太狠了吧"
        result = _parse_comments(text)
        assert len(result) == 1
        assert result[0]["emotionTag"] == "震惊"

    def test_parse_skip_preamble(self):
        """跳过非 [tag] 格式的说明文字"""
        text = "好的，以下是评论：\n[震惊]这也太狠了吧"
        result = _parse_comments(text)
        assert len(result) == 1
        assert result[0]["emotionTag"] == "震惊"

    def test_parse_numbered_list(self):
        text = "1. [震惊]这也太狠了吧\n2. [愤怒]气死了"
        result = _parse_comments(text)
        assert len(result) == 2

    def test_parse_empty(self):
        assert _parse_comments("") == []
        assert _parse_comments("没有评论") == []


class TestValidateComments:
    """LLM 返回校验测试"""

    def test_valid_comment_passes(self):
        comments = [{"emotionTag": "震惊", "text": "这也太狠了吧简直不敢相信啊这剧情反转"}]
        result = _validate_comments(comments, local_comments=[])
        assert len(result) == 1

    def test_too_short_discarded(self):
        """text < 15 字 → 丢弃"""
        comments = [{"emotionTag": "震惊", "text": "太狠了"}]
        result = _validate_comments(comments, local_comments=[])
        assert len(result) == 0

    def test_too_long_truncated(self):
        """text > 30 字 → 截断到 30"""
        comments = [{"emotionTag": "震惊", "text": "这也太狠了吧简直不敢相信啊这剧情反转得我措手不及完全没想到会是这样的发展"}]
        result = _validate_comments(comments, local_comments=[])
        assert len(result) == 1
        assert len(result[0]["text"]) <= 30

    def test_unknown_character_discarded(self):
        """引用未出场人物名 → 丢弃（ASCII 标点断开使 2 字名可被独立提取）"""
        # 贪婪正则 [一-鿿]{2,4} 在连续中文中无法提取 2 字名，
        # 需要非 CJK 字符断开。用 ASCII 空格分隔人名。
        comments = [{"emotionTag": "震惊", "text": "没想到 张三 做出这种可怕的事太吓人了"}]
        known_chars = ["李四", "王五"]
        result = _validate_comments(comments, local_comments=[], known_characters=known_chars)
        assert len(result) == 0

    def test_known_character_passes(self):
        """引用已出场人物名 → 通过"""
        comments = [{"emotionTag": "震惊", "text": "李四这个人真是太可怕了简直不敢信"}]
        known_chars = ["李四", "王五"]
        result = _validate_comments(comments, local_comments=[], known_characters=known_chars)
        assert len(result) == 1

    def test_no_known_characters_skip_check(self):
        """无 known_characters 时跳过人物校验"""
        comments = [{"emotionTag": "震惊", "text": "这个人真是太可怕了简直不敢相信啊"}]
        result = _validate_comments(comments, local_comments=[], known_characters=None)
        assert len(result) == 1

    def test_bigram_jaccard_dedup(self):
        """与本地模板相似 → 丢弃"""
        comments = [{"emotionTag": "震惊", "text": "这段剧情真是太震惊了"}]
        local = [{"emotionTag": "震惊", "text": "这段剧情真是让人震惊"}]
        result = _validate_comments(comments, local_comments=local)
        assert len(result) == 0


class TestScoreComment:
    """评论评分测试（6 分制）"""

    def test_perfect_comment(self):
        c = {"emotionTag": "震惊", "text": "他居然跪下来求原谅"}
        s = score_comment(c, "L1")
        assert s >= 4

    def test_generic_comment(self):
        c = {"emotionTag": "吐槽", "text": "这剧情太刺激了"}
        s = score_comment(c, "L1")
        assert s <= 3

    def test_no_tag(self):
        c = {"emotionTag": "", "text": "他居然跪下来求原谅"}
        s = score_comment(c, "L1")
        assert s <= 5  # 6 分制下无标签最多 5 分

    def test_sound_effect_comment_penalized(self):
        """纯声学描述评论应被封顶 1 分"""
        c = {"emotionTag": "震惊", "text": "这一声吼太炸了音效吓一跳"}
        s = score_comment(c, "L1")
        assert s == 1

    def test_single_sound_effect_without_plot_penalized(self):
        """单个声学词无剧情词也应被封顶 1 分"""
        c = {"emotionTag": "震惊", "text": "这音效也太炸了吧"}
        s = score_comment(c, "L1")
        assert s == 1

    def test_sound_effect_with_plot_not_penalized(self):
        """声学词+剧情词不应被封顶（一声吼 在列表中，但他+跪 有剧情信号）"""
        c = {"emotionTag": "震惊", "text": "他一声吼完直接跪了"}
        s = score_comment(c, "L1")
        assert s >= 3


class TestIsSimilarToAny:
    """bigram Jaccard 去重测试"""

    def test_similar_same_tag(self):
        c = {"emotionTag": "震惊", "text": "这段剧情真是太震惊了啊"}
        existing = [{"emotionTag": "震惊", "text": "这段剧情真是让人震惊了"}]
        assert is_similar_to_any(c, existing) is True

    def test_different_tag_no_match(self):
        c = {"emotionTag": "震惊", "text": "这段剧情太震惊了"}
        existing = [{"emotionTag": "愤怒", "text": "这段剧情让人震惊"}]
        assert is_similar_to_any(c, existing) is False

    def test_substring_match(self):
        c = {"emotionTag": "震惊", "text": "他居然跪下来求原谅了"}
        existing = [{"emotionTag": "愤怒", "text": "他居然跪下来求原谅了真是没想到"}]
        assert is_similar_to_any(c, existing) is True


class TestGenerateCommentsWithContext:
    """端到端：generate_comments_for_highlight 使用 context"""

    def test_fallback_without_context(self):
        """无 context 调用也能正常工作"""
        hl = {"timestamp": 30.0, "type": "L2", "confidence": 0.5}
        with patch("roast_generator._call_llm", return_value=""):
            comments = asyncio.run(generate_comments_for_highlight(hl))
        assert len(comments) > 0
        for c in comments:
            assert "emotionTag" in c
            assert "text" in c

    def test_with_context(self):
        """传入 context 时也能正常工作"""
        hl = {
            "timestamp": 30.0,
            "type": "L1",
            "confidence": 0.7,
            "emotion_label": "怒",
            "emotion_intensity": 0.8,
            "matched_keywords": ["滚出去"],
        }
        ctx = HighlightContext(
            timestamp=30.0,
            highlight_level="L1",
            dominant_emotion="怒",
            emotion_confidence=0.8,
            matched_keywords=["滚出去"],
            dialogue_snippets=[{"time": 28.0, "text": "你给我滚"}],
        )
        with patch("roast_generator._call_llm", return_value=""):
            comments = asyncio.run(generate_comments_for_highlight(hl, ctx=ctx))
        assert len(comments) > 0
