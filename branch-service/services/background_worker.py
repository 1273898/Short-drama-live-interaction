"""
后台分支生成任务编排
协调视频分析、分支生成、漫画生成、解说生成的完整流程
漫画生成失败时，分支仍保留为 generated 状态（无漫画但有文字）
"""

import os
import asyncio
import logging
from typing import Optional

from config import DEFAULT_BRANCH_COUNT, COMICS_DIR, VIDEO_ROOT
from database import get_db, set_generating
from services.video_analyzer import (
    get_video_path, get_video_info, extract_end_keyframe,
    extract_last_n_seconds_keyframes, frame_to_base64
)
from services.branch_generator import generate_branches_from_video_info
from services.comic_generator import generate_and_download_comic
from services.comic_describer import generate_panel_descriptions

logger = logging.getLogger(__name__)


async def generate_branches_for_episode(episode_id: int, force: bool = False):
    """
    为指定剧集生成剧情分支（后台任务）

    Args:
        episode_id: 剧集 ID
        force: 是否强制重新生成
    """
    db = await get_db()

    try:
        set_generating(episode_id, True)
        logger.info(f"开始为剧集 {episode_id} 生成分支...")

        # 1. 获取剧集信息
        cursor = await db.execute(
            "SELECT e.*, d.title as drama_title FROM episodes e JOIN dramas d ON e.drama_id = d.id WHERE e.id = ?",
            (episode_id,)
        )
        episode = await cursor.fetchone()
        if not episode:
            logger.error(f"剧集 {episode_id} 不存在")
            return

        video_path = get_video_path(episode["video_path"])
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return

        drama_title = episode["drama_title"]
        episode_number = episode["episode_number"]

        # 2. 检查是否已有分支
        if not force:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM branches WHERE episode_id = ? AND status = 'generated'",
                (episode_id,)
            )
            count = (await cursor.fetchone())[0]
            if count >= DEFAULT_BRANCH_COUNT:
                logger.info(f"剧集 {episode_id} 已有 {count} 个分支，跳过生成")
                return

        # 3. 提取关键帧
        logger.info("提取视频关键帧...")
        keyframe_base64 = None
        try:
            keyframe_bytes = extract_end_keyframe(video_path, offset_seconds=10)
            if keyframe_bytes:
                keyframe_base64 = frame_to_base64(keyframe_bytes)
                logger.info("关键帧提取成功")
        except Exception as e:
            logger.warning(f"关键帧提取失败（将使用纯文本模式）: {e}")

        # 4. 生成剧情概要（从关键帧分析）
        episode_summary = f"第{episode_number}集"
        last_subtitle = "（剧情分析中）"

        # 尝试从 metadata 缓存获取
        cursor = await db.execute(
            "SELECT summary, last_subtitle FROM episode_metadata WHERE episode_id = ?",
            (episode_id,)
        )
        meta = await cursor.fetchone()
        if meta and meta["summary"]:
            episode_summary = meta["summary"]
            last_subtitle = meta["last_subtitle"] or last_subtitle

        # 5. 调用 MIMO 生成分支
        logger.info(f"调用 MIMO 生成 {DEFAULT_BRANCH_COUNT} 个分支...")
        loop = asyncio.get_event_loop()
        branch_result = await loop.run_in_executor(
            None,
            lambda: generate_branches_from_video_info(
                drama_name=drama_title,
                episode_number=episode_number,
                episode_summary=episode_summary,
                last_subtitle=last_subtitle,
                keyframe_base64=keyframe_base64,
                num_branches=DEFAULT_BRANCH_COUNT,
            )
        )

        if "error" in branch_result:
            logger.error(f"分支生成失败: {branch_result}")
            return

        branches = branch_result.get("branches", [])
        logger.info(f"成功生成 {len(branches)} 个分支")

        # 6. 为每个分支生成漫画和解说（串行，每个之间等待）
        for i, branch_data in enumerate(branches):
            if i > 0:
                await asyncio.sleep(5)  # 分支之间等待 5 秒
            await _process_single_branch(db, episode_id, branch_data)

        await db.commit()
        logger.info(f"剧集 {episode_id} 分支生成完成")

    except Exception as e:
        logger.error(f"后台生成异常: {e}", exc_info=True)
    finally:
        set_generating(episode_id, False)


async def _process_single_branch(db, episode_id: int, branch_data: dict):
    """处理单个分支：生成漫画 + 解说 + 存入数据库"""
    try:
        # 1. 存入分支基本信息
        cursor = await db.execute(
            """INSERT INTO branches (episode_id, title, description, tone, preview, source, status)
               VALUES (?, ?, ?, ?, ?, 'auto', 'generating_comics')""",
            (
                episode_id,
                branch_data.get("title", "未命名"),
                branch_data.get("description", ""),
                branch_data.get("tone", ""),
                branch_data.get("preview", ""),
            )
        )
        branch_id = cursor.lastrowid
        await db.commit()

        logger.info(f"  生成分支 [{branch_data.get('title')}] 的动态漫...")

        # 2. 生成漫画图片
        loop = asyncio.get_event_loop()
        comic_result = await loop.run_in_executor(
            None,
            lambda: generate_and_download_comic(
                branch_id=branch_id,
                branch_title=branch_data.get("title", ""),
                branch_description=branch_data.get("description", ""),
                branch_preview=branch_data.get("preview", ""),
            )
        )

        has_comics = "error" not in comic_result
        local_paths = comic_result.get("local_paths", [])

        if not has_comics:
            logger.warning(f"  漫画生成失败，保留纯文字分支: {comic_result.get('error')}")

        # 3. 生成解说（无论漫画是否成功都生成文字）
        logger.info(f"  生成剧情解说...")
        descriptions = await loop.run_in_executor(
            None,
            lambda: generate_panel_descriptions(
                branch_title=branch_data.get("title", ""),
                branch_tone=branch_data.get("tone", ""),
                branch_description=branch_data.get("description", ""),
                branch_preview=branch_data.get("preview", ""),
            )
        )

        # 4. 存入漫画数据（有漫画就存路径，没漫画就只存文字）
        if has_comics:
            for i, (path, desc) in enumerate(zip(local_paths, descriptions)):
                await db.execute(
                    """INSERT INTO branch_comics (branch_id, panel_number, image_path, description)
                       VALUES (?, ?, ?, ?)""",
                    (branch_id, i + 1, path, desc.get("text", ""))
                )
        else:
            # 没有漫画图片，只存解说文字
            for i, desc in enumerate(descriptions):
                await db.execute(
                    """INSERT INTO branch_comics (branch_id, panel_number, image_path, description)
                       VALUES (?, ?, NULL, ?)""",
                    (branch_id, i + 1, desc.get("text", ""))
                )

        # 5. 更新分支状态为 generated（无论是否有漫画）
        await db.execute(
            "UPDATE branches SET status = 'generated' WHERE id = ?",
            (branch_id,)
        )
        await db.commit()

        status_str = "有漫画" if has_comics else "纯文字"
        logger.info(f"  分支 [{branch_data.get('title')}] 处理完成 ({status_str})")

    except Exception as e:
        logger.error(f"  分支处理异常: {e}", exc_info=True)
        # 异常时也尝试保留分支为 generated 状态
        try:
            await db.execute(
                "UPDATE branches SET status = 'generated' WHERE id = ? AND status != 'generated'",
                (branch_id,)
            )
            await db.commit()
        except:
            pass


async def generate_single_user_branch(branch_id: int):
    """为用户提交的单个分支生成漫画和解说"""
    db = await get_db()

    try:
        cursor = await db.execute("SELECT * FROM branches WHERE id = ?", (branch_id,))
        branch = await cursor.fetchone()
        if not branch:
            logger.error(f"分支 {branch_id} 不存在")
            return

        await _retry_branch_comics(db, branch)

    except Exception as e:
        logger.error(f"用户分支生成异常: {e}", exc_info=True)


async def retry_failed_comics():
    """重试所有没有漫画图片的分支"""
    db = await get_db()

    try:
        # 找出所有 status=generated 但没有漫画图片的分支
        cursor = await db.execute("""
            SELECT b.*
            FROM branches b
            WHERE b.status = 'generated'
            AND NOT EXISTS (
                SELECT 1 FROM branch_comics bc
                WHERE bc.branch_id = b.id AND bc.image_path IS NOT NULL
            )
        """)
        branches = await cursor.fetchall()

        if not branches:
            logger.info("没有需要重试的分支")
            return

        logger.info(f"找到 {len(branches)} 个需要重试漫画生成的分支")

        for branch in branches:
            logger.info(f"  重试分支 [{branch['title']}] (ID: {branch['id']})")
            await _retry_branch_comics(db, branch)
            # 每个分支之间等一下，避免连续触发限流
            await asyncio.sleep(2)

        logger.info("所有重试完成")

    except Exception as e:
        logger.error(f"重试任务异常: {e}", exc_info=True)


async def _retry_branch_comics(db, branch):
    """为单个分支重试漫画生成"""
    try:
        branch_id = branch["id"]

        # 删除旧的空漫画记录
        await db.execute("DELETE FROM branch_comics WHERE branch_id = ?", (branch_id,))
        await db.commit()

        logger.info(f"  生成分支 [{branch['title']}] 的动态漫...")

        loop = asyncio.get_event_loop()
        comic_result = await loop.run_in_executor(
            None,
            lambda: generate_and_download_comic(
                branch_id=branch_id,
                branch_title=branch["title"],
                branch_description=branch["description"],
                branch_preview=branch["preview"],
            )
        )

        has_comics = "error" not in comic_result
        local_paths = comic_result.get("local_paths", [])

        if not has_comics:
            logger.warning(f"  漫画生成仍然失败: {comic_result.get('error')}")
            # 重新存入空漫画记录
            for i in range(4):
                await db.execute(
                    """INSERT INTO branch_comics (branch_id, panel_number, image_path, description)
                       VALUES (?, ?, NULL, '')""",
                    (branch_id, i + 1)
                )
            await db.commit()
            return

        # 生成解说
        descriptions = await loop.run_in_executor(
            None,
            lambda: generate_panel_descriptions(
                branch_title=branch["title"],
                branch_tone=branch["tone"],
                branch_description=branch["description"],
                branch_preview=branch["preview"],
            )
        )

        # 存入漫画数据
        for i, (path, desc) in enumerate(zip(local_paths, descriptions)):
            await db.execute(
                """INSERT INTO branch_comics (branch_id, panel_number, image_path, description)
                   VALUES (?, ?, ?, ?)""",
                (branch_id, i + 1, path, desc.get("text", ""))
            )

        await db.commit()
        logger.info(f"  分支 [{branch['title']}] 重试成功，{len(local_paths)} 张漫画")

    except Exception as e:
        logger.error(f"  重试异常: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"用户分支生成异常: {e}", exc_info=True)
