"""
LLM 分支审核服务
审核用户提交的剧情分支是否合理
"""

import json
import logging
import requests
from typing import List, Optional

from config import MIMO_API_BASE, MIMO_API_KEY, MIMO_MODEL, VALIDATION_SCORE_THRESHOLD

logger = logging.getLogger(__name__)


def validate_user_branch(
    drama_name: str,
    episode_summary: str,
    user_title: str,
    user_description: str,
    user_tone: str,
    user_preview: str,
    existing_branches: Optional[List[dict]] = None,
) -> dict:
    """
    审核用户提交的分支是否合理

    Returns:
        {"valid": bool, "reason": str, "score": float}
    """
    existing_info = ""
    if existing_branches:
        existing_info = "\n【已有分支】\n"
        for b in existing_branches:
            existing_info += f"- {b['title']}：{b['description']}（{b['tone']}）\n"

    prompt = f"""你是一位专业的短剧内容审核员。请审核以下用户提交的剧情分支是否合理。

【剧集信息】
名称：{drama_name}
本集剧情：{episode_summary}
{existing_info}
【用户提交的分支】
标题：{user_title}
描述：{user_description}
风格：{user_tone}
走向：{user_preview}

【审核标准】
1. 分支是否符合剧情逻辑和设定（不能出现现代科技出现在古装剧中等）
2. 分支是否与已有分支有足够差异（不能重复或过于相似）
3. 分支是否有明确的叙事方向（不能含糊不清）
4. 分支是否包含不当内容（暴力、色情、歧视等）
5. 分支是否有趣味性和吸引力

请严格按照以下JSON格式返回，不要任何额外解释：
{{
  "valid": true,
  "reason": "审核通过原因或拒绝原因",
  "score": 0.85
}}

score 范围 0.0-1.0，0.6 以上为通过。"""

    headers = {
        "Authorization": f"Bearer {MIMO_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MIMO_MODEL,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "system",
                "content": "你是一位专业的短剧内容审核员，负责评估用户提交的剧情分支是否合理。请严格按照JSON格式返回。",
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            f"{MIMO_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 检查空内容
            if not content or not content.strip():
                logger.error(f"MIMO API 返回空内容，完整响应: {result}")
                return {"valid": False, "reason": "审核服务返回空结果", "score": 0}

            # 处理可能的 markdown 代码块
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])

            data = json.loads(content)

            # 应用阈值判断
            score = data.get("score", 0)
            if score < VALIDATION_SCORE_THRESHOLD:
                data["valid"] = False
                if "reason" not in data or not data["reason"]:
                    data["reason"] = f"评分 {score} 低于阈值 {VALIDATION_SCORE_THRESHOLD}"

            logger.info(f"分支审核结果: valid={data.get('valid')}, score={score}")
            return data

        else:
            logger.error(f"MIMO API 请求失败: {response.status_code}")
            return {"valid": False, "reason": f"审核服务异常: {response.status_code}", "score": 0}

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}")
        try:
            logger.error(f"原始响应内容: {repr(content)}")
            logger.error(f"原始API响应: {repr(response.text[:500])}")
        except:
            pass
        return {"valid": False, "reason": "审核结果解析失败", "score": 0}
    except Exception as e:
        logger.error(f"审核异常: {e}")
        return {"valid": False, "reason": f"审核异常: {str(e)}", "score": 0}
