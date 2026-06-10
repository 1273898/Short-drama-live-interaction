"""
剧情分支生成服务
使用 MIMO v2.5 模型根据短剧末尾内容生成剧情分支
使用 requests 直接调用，避免 openai/httpx 兼容性问题
"""

import json
import logging
import requests
from typing import Optional

from config import MIMO_API_BASE, MIMO_API_KEY, MIMO_MODEL

logger = logging.getLogger(__name__)


def generate_branches(
    drama_name: str,
    episode_number: int,
    episode_summary: str,
    last_subtitle: str,
    keyframe_base64: Optional[str] = None,
    drama_type: str = "古装/搞笑/逆袭",
    num_branches: int = 2,
) -> dict:
    """
    生成剧情分支

    Returns:
        {"episode_summary": "...", "branches": [{"id", "title", "description", "tone", "preview"}, ...]}
    """
    system_prompt = f"""你是一个专业的短剧编剧助手，擅长根据剧情内容生成引人入胜的剧情分支。

任务：分析短剧每集末尾的内容，生成 {num_branches} 个合理的剧情分支选项。

要求：
1. 分支必须基于当前剧情逻辑，不能脱离原有设定
2. 每个分支要有明确的方向和吸引力
3. 分支之间要有明显差异，不能过于相似
4. 要有悬念感，让用户有选择欲望
5. 严格按照指定 JSON 格式输出，不要任何额外解释"""

    user_text = f"""请根据以下短剧末尾内容，生成 {num_branches} 个剧情分支：

【剧集信息】
名称：{drama_name}
集数：第{episode_number}集
类型：{drama_type}

【本集剧情概要】
{episode_summary}

【末尾片段内容】
{last_subtitle}

输出要求：严格按照JSON格式输出，不要任何额外解释
{{
  "episode_summary": "本集剧情概要（50字以内）",
  "branches": [
    {{
      "id": "branch_1",
      "title": "分支标题（10字以内）",
      "description": "分支剧情描述（50字以内）",
      "tone": "热血/搞笑/温情/反转/悬疑",
      "preview": "选择此分支后的大致走向（30字以内）"
    }}
  ]
}}"""

    messages = [{"role": "system", "content": system_prompt}]

    if keyframe_base64:
        user_content = [
            {"type": "text", "text": user_text},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{keyframe_base64}"},
            },
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_text})

    headers = {
        "Authorization": f"Bearer {MIMO_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MIMO_MODEL,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.9,
        "top_p": 0.95,
    }

    try:
        response = requests.post(
            f"{MIMO_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )

        if response.status_code != 200:
            logger.error(f"MIMO API 请求失败: {response.status_code} - {response.text[:200]}")
            return {"error": "API请求失败", "status_code": response.status_code}

        result = response.json()
        response_text = result["choices"][0]["message"]["content"]
        logger.info(f"分支生成响应长度: {len(response_text)} 字符")

        # 处理 markdown 代码块包裹
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        parsed = json.loads(response_text)
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}, 原始响应: {response_text[:200]}")
        return {"error": "JSON解析失败", "raw_response": response_text[:500]}
    except Exception as e:
        logger.error(f"API调用失败: {e}")
        return {"error": "API调用失败", "details": str(e)}


def generate_branches_from_video_info(
    drama_name: str,
    episode_number: int,
    episode_summary: str,
    last_subtitle: str,
    keyframe_base64: Optional[str] = None,
    drama_type: str = "古装/搞笑/逆袭",
    num_branches: int = 2,
) -> dict:
    """便捷函数"""
    return generate_branches(
        drama_name=drama_name,
        episode_number=episode_number,
        episode_summary=episode_summary,
        last_subtitle=last_subtitle,
        keyframe_base64=keyframe_base64,
        drama_type=drama_type,
        num_branches=num_branches,
    )
