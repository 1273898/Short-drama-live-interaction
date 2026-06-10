"""吐槽生成器：为每个高光点预生成 3-5 条精灵吐槽评论（含评分+重新生成）"""

import os
import json
import logging
import time
import re
from typing import List, Dict, Optional, Tuple
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

_env = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))

# 全局 HTTP Session（连接池复用）
_session = None


def _get_session():
    global _session
    if _session is None:
        import requests
        _session = requests.Session()
    return _session


GENERIC_PHRASES = [
    "这剧情", "这段", "这部剧", "太刺激了", "太精彩了", "好看",
    "厉害", "牛", "绝了", "无语了", "服了", "醉了", "太强了", "无敌了"
]

TYPE_ATMOSPHERE_KEYWORDS = {
    "L1": ["震惊", "愤怒", "感动", "爆笑", "心疼", "他居然", "竟然", "没想到", "撕", "打", "跪", "哭", "吼"],
    "L2": ["吃瓜", "无语", "期待", "嘲笑", "心疼", "感觉", "这俩", "果然", "说", "看", "想", "悄悄"],
}


def _get_config(key: str, default: str = "") -> str:
    return _env.get(key) or os.environ.get(key, default)


def _call_llm(prompt: str, max_retries: int = 3) -> str:
    """调用 LLM API 生成吐槽"""
    base_url = _get_config("ANTHROPIC_BASE_URL", "").rstrip("/")
    api_key = _get_config("ANTHROPIC_AUTH_TOKEN")
    model = _get_config("ANTHROPIC_MODEL", "mimo-v2.5-pro")

    if not api_key:
        logger.warning("ANTHROPIC_AUTH_TOKEN 未设置，跳过吐槽生成")
        return ""

    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.8,
        "enable_thinking": False,
    }

    session = _get_session()
    for attempt in range(max_retries):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "").strip()
            if not content:
                content = msg.get("reasoning_content", "").strip()
            return content
        except Exception as e:
            logger.warning(f"吐槽生成 LLM 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1 * (attempt + 1))

    return ""


def _parse_comments(text: str) -> List[Dict[str, str]]:
    """解析 [情绪标签]正文 格式的吐槽列表

    处理 LLM 输出中的常见格式问题：
    - 序号前缀：'1. [震惊]xxx' -> '[震惊]xxx'
    - 多层嵌套：'[吐槽] - [震惊]xxx' -> '[震惊]xxx'
    - Preamble 文本（非 [tag] 格式的说明文字）直接跳过
    """
    import re
    results = []
    for line in text.split('\n'):
        trimmed = line.strip()
        if not trimmed:
            continue

        # 跳过明显的非评论行（prompt 片段、说明文字）
        skip_keywords = ["情绪标签", "可用标签", "好的示例", "严格要求", "输出格式",
                         "请从", "每条以", "不能提到", "每条不超过", "禁止使用",
                         "要有的", "需要5", "需要从", "应该从", "要从",
                         "1条即时", "共5条", "共4条", "不同情绪角度"]
        if any(kw in trimmed for kw in skip_keywords):
            continue

        # 去掉序号前缀：'1. [震惊]xxx' -> '[震惊]xxx'
        # 去掉破折号前缀：'- [震惊]xxx' -> '[震惊]xxx'
        cleaned = re.sub(r'^[\d]+[\.\)、]\s*[-–—]?\s*', '', trimmed)
        cleaned = re.sub(r'^[-–—]\s*', '', cleaned)

        # 处理多层嵌套：'[吐槽] - [震惊]xxx' -> 提取最后一个 [tag]
        if cleaned.startswith('['):
            # 查找嵌套的 [tag]
            nested_match = re.search(r'\[([^\]]+)\]\s*[-–—]?\s*\[([^\]]+)\]\s*(.*)', cleaned)
            if nested_match:
                tag = nested_match.group(2).strip()
                content = nested_match.group(3).strip()
                if tag and content:
                    results.append({"emotionTag": tag, "text": content})
                    continue

            tag_end = cleaned.find(']')
            tag = cleaned[1:tag_end].strip()
            content = cleaned[tag_end + 1:].strip()
            if tag and content:
                results.append({"emotionTag": tag, "text": content})
    return results


def score_comment(comment: Dict[str, str], highlight_type: str) -> int:
    """评分：5 分制，评估评论质量

    评分维度：
    1. 长度合理 (5-25字) +1
    2. 有情绪标签（非泛化） +1
    3. 提到具体人物/动作（非泛化） +1
    4. 无泛化短语 +1
    5. 匹配高光类型氛围 +1
    """
    score = 0
    text = comment.get("text", "")
    tag = comment.get("emotionTag", "")

    # 1. 长度合理
    if 5 <= len(text) <= 25:
        score += 1

    # 2. 有情绪标签
    if tag and tag != "吐槽":
        score += 1

    # 3. 提到具体人物/动作
    has_specific = bool(re.search(
        r'[他她它我你]|[A-Z][a-z]+|[一-龥]{2,4}(?:说|做|看|打|哭|笑|跑|走|跪|喊|撕|推|拉|抱|踢|瞪|指|咬|撞)',
        text
    ))
    if has_specific:
        score += 1

    # 4. 无泛化短语
    has_generic = any(phrase in text for phrase in GENERIC_PHRASES)
    if not has_generic:
        score += 1

    # 5. 匹配高光类型氛围
    keywords = TYPE_ATMOSPHERE_KEYWORDS.get(highlight_type, [])
    if keywords:
        type_match = any(kw in text for kw in keywords)
    else:
        type_match = True
    if type_match:
        score += 1

    return score


def is_similar_to_any(comment: Dict[str, str], existing: List[Dict[str, str]]) -> bool:
    """检查评论是否与已有评论过于相似（使用集合交集优化）"""
    text = comment.get("text", "")
    tag = comment.get("emotionTag", "")

    for other in existing:
        other_text = other.get("text", "")
        other_tag = other.get("emotionTag", "")

        # 同标签 + 文字重叠 > 50%（用集合交集替代逐字符遍历）
        if tag == other_tag and text and other_text:
            shorter = text if len(text) <= len(other_text) else other_text
            longer = text if len(text) > len(other_text) else other_text
            shorter_set = set(shorter)
            longer_set = set(longer)
            overlap = len(shorter_set & longer_set)
            if overlap / max(len(shorter_set), 1) > 0.5:
                return True

        # 包含关系
        if len(text) > 4 and text in other_text:
            return True
        if len(other_text) > 4 and other_text in text:
            return True

    return False


def generate_comments_for_highlight(
    highlight: Dict,
    drama_title: str = "",
    episode_title: str = "",
    count: int = 5
) -> List[Dict[str, str]]:
    """为单个高光点生成吐槽评论（含评分+重新生成）

    流程：
    1. 生成 count 条评论
    2. 每条评分（5分制）
    3. ≤3分的评论重新生成（最多1次）
    4. 去重（相似度检查）
    5. 按分数排序，保留 3-5 条
    """
    roast_text = highlight.get("roast_text", "")
    hl_type = highlight.get("type", "L2")
    timestamp = highlight.get("timestamp", 0)
    subtitle_context = highlight.get("subtitle_context", "")

    type_desc = {
        "L1": "极高能剧情（高潮/反转/冲突爆发）",
        "L2": "高能剧情（关键对话/情感转折）",
    }.get(hl_type, "看点")

    scene_desc = roast_text if roast_text else f"正在播放{type_desc}片段"

    # 构建上下文
    if subtitle_context:
        context_block = f"""高光时刻前后的真实台词（约第{int(timestamp)}秒附近）：
\"{subtitle_context}\"

场景概括：{scene_desc}"""
    else:
        context_block = f"刚刚发生的剧情（约第{int(timestamp)}秒）：{scene_desc}"

    # ===== 第一轮：生成 =====
    prompt = f"""你是正在和用户一起看短剧《{drama_title}》（{episode_title}）的精灵"灵灵"。

{context_block}

请从{count}个不同情绪角度，各写1条即时反应吐槽（共{count}条）。

严格要求：
1. 必须引用该高光点中实际出现的人物名字或具体动作/台词
2. 绝对不能提到未出场的人物、未发生的事件
3. 如果有真实台词，优先基于台词内容吐槽，不要编造未出现的台词
4. 禁止使用"这剧情"、"这段"、"这部剧"、"太刺激了"等笼统表述
5. 每条不超过20字，口语化，有梗
6. 要有追剧的代入感，像真的在跟朋友一起看
7. 每条情绪角度不同，不要重复

输出格式：每条以[情绪标签]开头，换行分隔。
可用标签：震惊、无语、吃瓜、爆笑、感动、心疼、期待、愤怒、嘲笑。

好的示例：
[震惊]他居然把亲爹的遗嘱撕了？
[感动]小雪为了救弟弟跪了一夜
[吃瓜]这俩人的眼神交流也太暧昧了
[无语]堂堂总裁居然被一碗面骗了
[爆笑]管家这表情笑死我了哈哈哈
[期待]感觉大哥要黑化了！"""

    result_text = _call_llm(prompt)
    if not result_text:
        return []

    comments = _parse_comments(result_text)
    if not comments:
        return []

    logger.info(f"第一轮生成: {len(comments)} 条, ts={timestamp}s")

    # ===== 第二轮：评分 + 重新生成 =====
    scored: List[Tuple[Dict[str, str], int]] = []
    rejected: List[Dict[str, str]] = []

    for c in comments:
        s = score_comment(c, hl_type)
        scored.append((c, s))
        if s <= 3:
            rejected.append(c)

    logger.info(f"评分结果: {[(c['text'], s) for c, s in scored]}")

    # 重新生成低分评论（最多 1 次）
    if rejected and len([s for _, s in scored if s >= 4]) < 3:
        existing_texts = [c["text"] for c, _ in scored]
        retry_prompt = f"""《{drama_title}》{episode_title}。
{context_block}

已有的吐槽（不能重复这些意思）：
{chr(10).join(f'- {t}' for t in existing_texts)}

请再写{len(rejected) + 2}条不同角度的即时反应吐槽，避免与上面重复。
每条以[情绪标签]开头，不超过20字，提到该场景中实际出现的人物或动作。
不能提到未出场的人物或未发生的事件。"""

        retry_text = _call_llm(retry_prompt)
        if retry_text:
            retry_comments = _parse_comments(retry_text)
            for rc in retry_comments:
                s = score_comment(rc, hl_type)
                if s >= 4 and not is_similar_to_any(rc, [c for c, _ in scored]):
                    scored.append((rc, s))
                    logger.info(f"重新生成通过: {rc['text']} ({s}分)")

    # ===== 第三轮：去重 + 排序 =====
    # 去重：相似度检查
    unique: List[Tuple[Dict[str, str], int]] = []
    for c, s in scored:
        if not is_similar_to_any(c, [u for u, _ in unique]):
            unique.append((c, s))

    # 按分数降序，保留 3-5 条
    unique.sort(key=lambda x: x[1], reverse=True)
    final = [c for c, s in unique[:5]]

    logger.info(f"最终评论: {len(final)} 条, ts={timestamp}s, scores={[s for _, s in unique[:5]]}")
    return final


def generate_comments_for_highlights(
    highlights: List[Dict],
    drama_title: str = "",
    episode_title: str = ""
) -> List[Dict]:
    """为一组高光点批量生成吐槽，返回带 comments 字段的高光点列表"""
    if not highlights:
        return highlights

    for hl in highlights:
        count = {"L1": 5, "L2": 4}.get(hl.get("type", "L2"), 4)
        try:
            comments = generate_comments_for_highlight(
                highlight=hl,
                drama_title=drama_title,
                episode_title=episode_title,
                count=count
            )
            hl["comments"] = comments
        except Exception as e:
            logger.error(f"吐槽生成失败: timestamp={hl.get('timestamp')}, error={e}")
            hl["comments"] = []

    return highlights
