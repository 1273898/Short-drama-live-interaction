"""
动态漫剧情解说生成服务
根据剧情分支文字为每张漫画生成解说
适配自 test/generate_comic_descriptions.py
"""

import json
import logging
import requests
from typing import List

from config import MIMO_API_BASE, MIMO_API_KEY, MIMO_MODEL

logger = logging.getLogger(__name__)


def generate_panel_descriptions(
    branch_title: str,
    branch_tone: str,
    branch_description: str,
    branch_preview: str,
) -> List[dict]:
    """
    为一个分支的 4 张漫画生成剧情解说

    Returns:
        [{"panel": 1, "text": "..."}, ...] 共 4 个元素
    """
    prompt = f"""你是一位专业的短剧解说文案作者。请为以下剧情分支的动态漫生成4段剧情解说。

【分支信息】
标题：{branch_title}
风格：{branch_tone}
剧情：{branch_description}
走向：{branch_preview}

【要求】
为4张连续的漫画生成解说，每张代表剧情的一个阶段：
- 第1张：场景建立，人物登场，引出冲突
- 第2张：矛盾升级，冲突加剧
- 第3张：高潮时刻，关键转折
- 第4张：悬念收尾，吸引观众继续观看

每段解说要求：
1. 50-80字
2. 语言生动，有画面感
3. 符合{branch_tone}风格
4. 适合做漫画旁白或短视频解说

请严格按照以下格式返回，每行一个，不要其他内容：
1. 第1张解说文字
2. 第2张解说文字
3. 第3张解说文字
4. 第4张解说文字"""

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
                "content": "你是一位专业的短剧解说文案作者，擅长用生动的语言描述剧情。",
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            f"{MIMO_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]

            panels = []
            lines = content.strip().split("\n")
            for i, line in enumerate(lines):
                text = line.strip()
                # 去掉序号前缀
                if text.startswith(f"{i + 1}."):
                    text = text[len(f"{i + 1}."):].strip()
                elif text.startswith(f"第{i + 1}张"):
                    text = text[len(f"第{i + 1}张"):].strip("：:").strip()
                panels.append({"panel": i + 1, "text": text})

            while len(panels) < 4:
                panels.append({"panel": len(panels) + 1, "text": "（待补充）"})

            logger.info(f"为分支 [{branch_title}] 生成 {len(panels[:4])} 段解说")
            return panels[:4]
        else:
            logger.error(f"MIMO API 请求失败: {response.status_code}")
            return [{"panel": i + 1, "text": f"生成失败: {response.status_code}"} for i in range(4)]

    except Exception as e:
        logger.error(f"解说生成异常: {e}")
        return [{"panel": i + 1, "text": f"异常: {str(e)}"} for i in range(4)]
