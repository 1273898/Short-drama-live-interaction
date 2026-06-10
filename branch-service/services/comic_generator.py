"""
动态漫生成服务
使用 doubao-seedream-5-0 模型根据剧情分支生成漫画
含 429 限流重试机制
"""

import os
import time
import json
import logging
import requests
from typing import List

from config import ARK_API_BASE, ARK_API_KEY, ARK_MODEL, COMICS_DIR

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
BASE_DELAY = 10  # 秒


def generate_comic_from_branch(
    branch_title: str,
    branch_description: str,
    branch_preview: str,
    drama_type: str = "古装/搞笑/逆袭",
    num_images: int = 4,
) -> dict:
    """
    根据剧情分支生成动态漫，含 429 重试

    Returns:
        {"success": True, "image_urls": [...], "num_images": int} 或 {"error": "..."}
    """
    prompt = f"""请生成一组共{num_images}张连贯的漫画风格插画，用于展示短剧剧情分支：

【剧情分支信息】
标题：{branch_title}
描述：{branch_description}
走向：{branch_preview}
风格：{drama_type}

【要求】
1. 采用精美漫画风格，画面有电影质感
2. {num_images}张图片要形成连贯的故事序列
3. 第1张：展示当前场景和人物
4. 第2张：展示冲突或转折
5. 第3张：展示高潮或关键选择
6. 第4张：展示悬念或结果预示
7. 符合{drama_type}的风格特点
8. 人物表情生动，场景细节丰富
9. 画面要有叙事感，让观众能感受到故事张力"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ARK_API_KEY}",
    }

    payload = {
        "model": ARK_MODEL,
        "prompt": prompt,
        "sequential_image_generation": "auto",
        "sequential_image_generation_options": {
            "max_images": num_images,
        },
        "response_format": "url",
        "size": "2K",
        "stream": False,
        "watermark": False,
    }

    for attempt in range(MAX_RETRIES):
        try:
            logger.info(f"正在为分支 [{branch_title}] 生成动态漫... (尝试 {attempt + 1}/{MAX_RETRIES})")
            response = requests.post(ARK_API_BASE, headers=headers, json=payload, timeout=300)

            if response.status_code == 200:
                result = response.json()
                image_urls = [item.get("url") for item in result.get("data", []) if item.get("url")]
                logger.info(f"成功生成 {len(image_urls)} 张图片")
                return {"success": True, "image_urls": image_urls, "num_images": len(image_urls)}

            if response.status_code == 429:
                # 限流，指数退避重试
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Ark API 限流 (429)，{delay}秒后重试... (尝试 {attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
                continue

            # 其他错误不重试
            logger.error(f"Ark API 请求失败: {response.status_code} - {response.text[:200]}")
            return {"error": "请求失败", "status_code": response.status_code}

        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f"Ark API 请求异常，{delay}秒后重试: {e}")
                time.sleep(delay)
                continue
            logger.error(f"Ark API 请求异常: {e}")
            return {"error": "请求异常", "details": str(e)}

    return {"error": "请求失败，已重试最大次数", "status_code": 429}


def download_images(image_urls: List[str], branch_id: int, prefix: str = "comic") -> List[str]:
    """
    下载图片到本地 static/branch_comics/{branch_id}/ 目录

    Returns:
        保存的文件路径列表（相对于 static 的路径）
    """
    save_dir = os.path.join(COMICS_DIR, str(branch_id))
    os.makedirs(save_dir, exist_ok=True)

    saved_paths = []
    for i, url in enumerate(image_urls):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()

            filename = f"{prefix}_{i + 1}.png"
            filepath = os.path.join(save_dir, filename)

            with open(filepath, "wb") as f:
                f.write(resp.content)

            # 存储相对于 static 目录的路径
            relative_path = f"branch_comics/{branch_id}/{filename}"
            saved_paths.append(relative_path)
            logger.info(f"图片已保存: {filepath}")

        except Exception as e:
            logger.error(f"下载图片 {i + 1} 失败: {e}")

    return saved_paths


def generate_and_download_comic(
    branch_id: int,
    branch_title: str,
    branch_description: str,
    branch_preview: str,
    drama_type: str = "古装/搞笑/逆袭",
) -> dict:
    """
    生成动态漫并下载到本地

    Returns:
        {"success": True, "local_paths": [...], "image_urls": [...]} 或 {"error": "..."}
    """
    result = generate_comic_from_branch(
        branch_title=branch_title,
        branch_description=branch_description,
        branch_preview=branch_preview,
        drama_type=drama_type,
    )

    if "error" in result:
        return result

    image_urls = result.get("image_urls", [])
    local_paths = download_images(image_urls, branch_id)

    return {
        "success": True,
        "local_paths": local_paths,
        "image_urls": image_urls,
        "num_images": len(local_paths),
    }
