"""drama_pacing + event_cluster 连贯性合并测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from drama_pacing import detect_emotion_reversals, compute_pacing_score, compute_pacing_scores
from event_cluster import merge_coherent_events, _assign_emotion_to_events


class TestDetectEmotionReversals:
    """情绪反转点识别测试"""

    def test_empty_timeline(self):
        assert detect_emotion_reversals([]) == []

    def test_single_segment(self):
        tl = [{"time": 5.0, "label": "喜", "confidence": 0.8}]
        assert detect_emotion_reversals(tl) == []

    def test_no_reversal_same_label(self):
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.9},
            {"time": 10.0, "label": "喜", "confidence": 0.8},
        ]
        assert detect_emotion_reversals(tl) == []

    def test_reversal_different_labels_high_confidence(self):
        """相邻段情绪不同且置信度均 > 0.6 → 识别为反转点"""
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 10.0, "label": "怒", "confidence": 0.7},
        ]
        reversals = detect_emotion_reversals(tl)
        assert len(reversals) == 1
        r = reversals[0]
        assert r["from_label"] == "喜"
        assert r["to_label"] == "怒"
        assert r["time"] == pytest.approx(7.5)
        assert r["confidence"] == pytest.approx(0.75)

    def test_no_reversal_low_confidence(self):
        """一方置信度 <= 0.6 → 不识别"""
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.5},
            {"time": 10.0, "label": "怒", "confidence": 0.8},
        ]
        assert detect_emotion_reversals(tl) == []

    def test_no_reversal_both_low(self):
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.3},
            {"time": 10.0, "label": "怒", "confidence": 0.4},
        ]
        assert detect_emotion_reversals(tl) == []

    def test_multiple_reversals(self):
        tl = [
            {"time": 2.0, "label": "喜", "confidence": 0.9},
            {"time": 6.0, "label": "怒", "confidence": 0.8},
            {"time": 12.0, "label": "悲", "confidence": 0.7},
        ]
        reversals = detect_emotion_reversals(tl)
        assert len(reversals) == 2
        assert reversals[0]["from_label"] == "喜"
        assert reversals[0]["to_label"] == "怒"
        assert reversals[1]["from_label"] == "怒"
        assert reversals[1]["to_label"] == "悲"

    def test_boundary_confidence_exactly_06(self):
        """置信度 == 0.6 不满足 > 0.6 条件"""
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.6},
            {"time": 10.0, "label": "怒", "confidence": 0.7},
        ]
        assert detect_emotion_reversals(tl) == []

    def test_unsorted_timeline(self):
        """输入未排序也应正确处理"""
        tl = [
            {"time": 10.0, "label": "怒", "confidence": 0.8},
            {"time": 5.0, "label": "喜", "confidence": 0.9},
        ]
        reversals = detect_emotion_reversals(tl)
        assert len(reversals) == 1
        assert reversals[0]["from_label"] == "喜"


class TestComputePacingScore:
    """节奏分计算测试"""

    def test_empty_timeline(self):
        assert compute_pacing_score(10.0, []) == 0.0

    def test_single_segment(self):
        tl = [{"time": 5.0, "label": "喜", "confidence": 0.8}]
        assert compute_pacing_score(10.0, tl) == 0.0

    def test_no_reversals(self):
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 10.0, "label": "喜", "confidence": 0.9},
        ]
        assert compute_pacing_score(8.0, tl) == 0.0

    def test_nearby_reversal_gives_bonus(self):
        """附近有反转点时，节奏分 > 0"""
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.9},
            {"time": 10.0, "label": "怒", "confidence": 0.8},
        ]
        score = compute_pacing_score(8.0, tl, tolerance=5.0)
        assert score > 0.0

    def test_far_away_reversal_zero(self):
        """反转点在 tolerance 范围外 → 0 分"""
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.9},
            {"time": 10.0, "label": "怒", "confidence": 0.8},
        ]
        # 反转点在 7.5s，查询 50s → 超出 tolerance=5
        score = compute_pacing_score(50.0, tl, tolerance=5.0)
        assert score == 0.0

    def test_multiple_reversals_density(self):
        """多个反转点附近 → 高密度 → 高分"""
        tl = [
            {"time": 2.0, "label": "喜", "confidence": 0.9},
            {"time": 4.0, "label": "怒", "confidence": 0.8},
            {"time": 6.0, "label": "喜", "confidence": 0.7},
        ]
        # 反转点在 3.0 和 5.0，查询 4.0 → 两个都在 tolerance=5 内
        score = compute_pacing_score(4.0, tl, tolerance=5.0)
        assert score > 0.15  # density_score + bonus

    def test_score_capped_at_1(self):
        """节奏分不超过 1.0"""
        tl = [
            {"time": 1.0, "label": "喜", "confidence": 0.9},
            {"time": 2.0, "label": "怒", "confidence": 0.9},
            {"time": 3.0, "label": "喜", "confidence": 0.9},
            {"time": 4.0, "label": "怒", "confidence": 0.9},
            {"time": 5.0, "label": "喜", "confidence": 0.9},
        ]
        score = compute_pacing_score(3.0, tl, tolerance=5.0)
        assert score <= 1.0


class TestComputePacingScores:
    """批量节奏分测试"""

    def test_batch_returns_correct_length(self):
        events = [
            {"peak_time": 5.0},
            {"peak_time": 15.0},
            {"peak_time": 25.0},
        ]
        tl = [
            {"time": 5.0, "label": "喜", "confidence": 0.9},
            {"time": 20.0, "label": "怒", "confidence": 0.8},
        ]
        scores = compute_pacing_scores(events, tl)
        assert len(scores) == 3

    def test_batch_empty_timeline(self):
        events = [{"peak_time": 5.0}, {"peak_time": 15.0}]
        scores = compute_pacing_scores(events, [])
        assert scores == [0.0, 0.0]

    def test_batch_none_timeline(self):
        events = [{"peak_time": 5.0}]
        scores = compute_pacing_scores(events, None)
        assert scores == [0.0]


class TestMergeCoherentEvents:
    """剧情连贯性合并测试"""

    def test_empty_events(self):
        assert merge_coherent_events([]) == []

    def test_single_event(self):
        events = [{"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5}]
        result = merge_coherent_events(events)
        assert len(result) == 1

    def test_no_emotion_timeline(self):
        """无情绪 timeline → 不合并"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
            {"peak_time": 8.0, "start_time": 6.0, "end_time": 10.0, "acoustic_score": 0.6},
        ]
        result = merge_coherent_events(events, emotion_timeline=None)
        assert len(result) == 2

    def test_merge_same_emotion_close_gap(self):
        """间隔 < 10s 且情绪相同 → 合并"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
            {"peak_time": 8.0, "start_time": 6.0, "end_time": 10.0, "acoustic_score": 0.6},
        ]
        emotion_timeline = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 8.0, "label": "喜", "confidence": 0.7},
        ]
        result = merge_coherent_events(events, emotion_timeline=emotion_timeline, merge_gap=10.0)
        assert len(result) == 1
        merged = result[0]
        assert merged["start_time"] == 3.0
        assert merged["end_time"] == 10.0
        # score = max(0.5, 0.6) + 0.05 = 0.65
        assert merged["acoustic_score"] == pytest.approx(0.65, abs=0.01)

    def test_no_merge_different_emotion(self):
        """情绪不同 → 不合并"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
            {"peak_time": 8.0, "start_time": 6.0, "end_time": 10.0, "acoustic_score": 0.6},
        ]
        emotion_timeline = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 8.0, "label": "怒", "confidence": 0.7},
        ]
        result = merge_coherent_events(events, emotion_timeline=emotion_timeline, merge_gap=10.0)
        assert len(result) == 2

    def test_no_merge_large_gap(self):
        """间隔 >= 10s → 不合并"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
            {"peak_time": 20.0, "start_time": 18.0, "end_time": 22.0, "acoustic_score": 0.6},
        ]
        emotion_timeline = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 20.0, "label": "喜", "confidence": 0.7},
        ]
        result = merge_coherent_events(events, emotion_timeline=emotion_timeline, merge_gap=10.0)
        assert len(result) == 2

    def test_peak_time_keeps_higher_score(self):
        """合并后 peak_time 保留分数更高的段"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.7},
            {"peak_time": 8.0, "start_time": 6.0, "end_time": 10.0, "acoustic_score": 0.4},
        ]
        emotion_timeline = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 8.0, "label": "喜", "confidence": 0.7},
        ]
        result = merge_coherent_events(events, emotion_timeline=emotion_timeline, merge_gap=10.0)
        assert len(result) == 1
        assert result[0]["peak_time"] == 5.0  # 高分段的 peak_time

    def test_temp_field_cleaned(self):
        """合并后 _emotion_label 临时字段应被清除"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
        ]
        emotion_timeline = [{"time": 5.0, "label": "喜", "confidence": 0.8}]
        result = merge_coherent_events(events, emotion_timeline=emotion_timeline)
        for evt in result:
            assert "_emotion_label" not in evt

    def test_coherence_bonus_applied(self):
        """自定义 coherence_bonus 参数生效"""
        events = [
            {"peak_time": 5.0, "start_time": 3.0, "end_time": 7.0, "acoustic_score": 0.5},
            {"peak_time": 8.0, "start_time": 6.0, "end_time": 10.0, "acoustic_score": 0.6},
        ]
        emotion_timeline = [
            {"time": 5.0, "label": "喜", "confidence": 0.8},
            {"time": 8.0, "label": "喜", "confidence": 0.7},
        ]
        result = merge_coherent_events(
            events, emotion_timeline=emotion_timeline,
            merge_gap=10.0, coherence_bonus=0.1,
        )
        assert len(result) == 1
        assert result[0]["acoustic_score"] == pytest.approx(0.7, abs=0.01)
