"""融合决策引擎：合并规则引擎和 AI 引擎的结果，输出最终高光点列表（含质量打分）"""

import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def merge_highlights(
    rule_highlights: List[Dict],
    ai_highlights: List[Dict],
    window_size: float = 15.0
) -> List[Dict]:
    """合并两个引擎的结果，15秒窗口内对齐"""

    rule_sorted = sorted(rule_highlights, key=lambda x: x["timestamp"])
    ai_sorted = sorted(ai_highlights, key=lambda x: x["timestamp"])

    merged = []
    used_ai = set()

    for rh in rule_sorted:
        best_match = None
        best_distance = float('inf')

        for i, ah in enumerate(ai_sorted):
            if i in used_ai:
                continue
            distance = abs(rh["timestamp"] - ah["timestamp"])
            if distance <= window_size and distance < best_distance:
                best_distance = distance
                best_match = i

        if best_match is not None:
            ah = ai_sorted[best_match]
            used_ai.add(best_match)
            confidence = rh["confidence"] * 0.6 + ah["confidence"] * 0.4
            merged.append({
                "timestamp": (rh["timestamp"] + ah["timestamp"]) / 2,
                "confidence": confidence,
                "dual_engine": True,
                "reason": ah.get("reason", ""),
                "ai_intensity": ah.get("confidence", 0),
            })
        else:
            merged.append({
                "timestamp": rh["timestamp"],
                "confidence": rh["confidence"] * 0.75,
                "dual_engine": False,
                "reason": "",
                "ai_intensity": 0,
            })

    for i, ah in enumerate(ai_sorted):
        if i not in used_ai:
            merged.append({
                "timestamp": ah["timestamp"],
                "confidence": ah["confidence"] * 0.4,
                "dual_engine": False,
                "reason": ah.get("reason", ""),
                "ai_intensity": ah.get("confidence", 0),
            })

    return merged


_SEMANTIC_KEYWORDS = re.compile(r'角色|对白|冲突|反转|告白|打斗|追车|爆炸|死亡|亲[吻嘴]|拥抱|争吵|揭秘|背叛|和好|分手|跳崖|中毒|误会')


def score_highlight(h: Dict) -> int:
    """对单个高光点进行质量打分（1-5分）

    评分维度：
    - 双引擎命中（+2）：音视频+AI同时检测到，可信度高
    - 置信度（0-2）：confidence >= 0.85 -> 2分, >= 0.7 -> 1分
    - 原因描述质量（0-1）：长度>15字符且包含语义关键词 -> 1分
    - AI 引擎强度（0-1）：ai_intensity >= 0.7 -> 1分

    总分 1-5 分
    """
    score = 1  # 基础分

    # 双引擎命中
    if h.get("dual_engine", False):
        score += 2

    # 置信度
    confidence = h.get("confidence", 0)
    if confidence >= 0.85:
        score += 2
    elif confidence >= 0.7:
        score += 1

    # 原因描述质量：长度>15字符且包含语义关键词
    reason = h.get("reason", "")
    if reason and len(reason) > 15 and _SEMANTIC_KEYWORDS.search(reason):
        score += 1

    # AI 引擎强度
    if h.get("ai_intensity", 0) >= 0.7:
        score += 1

    return min(score, 5)


def classify_by_score(score: int) -> str:
    """根据质量打分分级：L1(4-5分), L2(2-3分), L3(1分)"""
    if score >= 4:
        return "L1"
    elif score >= 2:
        return "L2"
    else:
        return "L3"


def classify_highlights(highlights: List[Dict]) -> List[Dict]:
    """对高光点进行质量打分和分级，只保留 L1 和 L2"""

    classified = []
    for h in highlights:
        score = score_highlight(h)
        level = classify_by_score(score)

        # 只保留 L1 和 L2
        if level not in ("L1", "L2"):
            continue

        entry = {
            "timestamp": h["timestamp"],
            "type": level,
            "level": level,
            "confidence": h["confidence"],
            "score": score,
        }

        reason = h.get("reason", "")
        if reason:
            entry["roast_text"] = reason

        classified.append(entry)

    return classified


def deduplicate_highlights(highlights: List[Dict], window_size: float = 30.0) -> List[Dict]:
    """去重：30秒内多个高光点只保留质量分数最高的"""
    if not highlights:
        return []

    sorted_highlights = sorted(highlights, key=lambda x: x["timestamp"])
    result = []
    current_group = [sorted_highlights[0]]

    for h in sorted_highlights[1:]:
        if h["timestamp"] - current_group[-1]["timestamp"] <= window_size:
            current_group.append(h)
        else:
            best = max(current_group, key=lambda x: x.get("score", x.get("confidence", 0)))
            result.append(best)
            current_group = [h]

    if current_group:
        best = max(current_group, key=lambda x: x.get("score", x.get("confidence", 0)))
        result.append(best)

    return result


def fuse_highlights(
    rule_highlights: List[Dict],
    ai_highlights: List[Dict],
    max_highlights: int = 5,
    video_duration: Optional[float] = None
) -> List[Dict]:
    """融合两个引擎的结果，输出最终高光点列表（最多max_highlights个）

    流程：合并 -> 打分+分级 -> 去重 -> 限制数量 -> 间隔过滤

    Args:
        video_duration: 视频时长（秒），用于计算最小间隔（时长 × 20%）
    """

    # 1. 合并对齐
    merged = merge_highlights(rule_highlights, ai_highlights)

    # 2. 打分 + 分级（含过滤，只保留 L1/L2）
    classified = classify_highlights(merged)

    # 3. 去重
    final = deduplicate_highlights(classified)

    # 4. 限制数量：按分数排序，取前 max_highlights 个
    if len(final) > max_highlights:
        final = sorted(final, key=lambda x: x.get("score", 0), reverse=True)[:max_highlights]
        final = sorted(final, key=lambda x: x.get("timestamp", 0))

    # 5. 间隔过滤：相邻高光点间隔 < 视频时长 × 20% 时，丢弃分数较低的
    if video_duration and video_duration > 0 and len(final) > 1:
        min_gap = video_duration * 0.2
        logger.info(f"间隔过滤: 视频时长={video_duration:.1f}s, 最小间隔={min_gap:.1f}s")
        filtered = [final[0]]
        for h in final[1:]:
            prev = filtered[-1]
            gap = h["timestamp"] - prev["timestamp"]
            if gap < min_gap:
                # 间隔不足，保留分数更高的那个
                prev_score = prev.get("score", 0)
                curr_score = h.get("score", 0)
                if curr_score > prev_score:
                    filtered[-1] = h
                    logger.info(f"  丢弃 t={prev['timestamp']:.1f}s(score={prev_score}), 保留 t={h['timestamp']:.1f}s(score={curr_score}), 间隔={gap:.1f}s < {min_gap:.1f}s")
                else:
                    logger.info(f"  丢弃 t={h['timestamp']:.1f}s(score={curr_score}), 保留 t={prev['timestamp']:.1f}s(score={prev_score}), 间隔={gap:.1f}s < {min_gap:.1f}s")
            else:
                filtered.append(h)
        final = filtered

    # 日志输出最终结果
    for i, h in enumerate(final):
        logger.info(f"  高光[{i}]: t={h['timestamp']:.1f}s, score={h.get('score', '?')}, level={h.get('type', '?')}")

    return final