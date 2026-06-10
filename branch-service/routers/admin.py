"""
管理员 API 路由
用于手动触发分支生成和重试
"""

import asyncio
import logging
from fastapi import APIRouter

from database import is_generating, set_generating, get_db
from models import GenerateResponse
from services.background_worker import generate_branches_for_episode, retry_failed_comics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin")


@router.post("/generate/{episode_id}", response_model=GenerateResponse)
async def trigger_generation(episode_id: int, force: bool = False):
    """手动触发后台分支生成"""
    if is_generating(episode_id):
        return GenerateResponse(
            status="already_generating",
            episode_id=episode_id,
            message=f"剧集 {episode_id} 正在生成中，请稍后",
        )

    asyncio.create_task(generate_branches_for_episode(episode_id, force=force))

    return GenerateResponse(
        status="started",
        episode_id=episode_id,
        message=f"剧集 {episode_id} 分支生成已启动",
    )


@router.post("/retry-comics", response_model=GenerateResponse)
async def retry_all_failed_comics():
    """重试所有没有漫画图片的分支"""
    asyncio.create_task(retry_failed_comics())
    return GenerateResponse(
        status="started",
        episode_id=0,
        message="重试任务已启动，正在为缺少漫画的分支重新生成图片",
    )


@router.get("/stats")
async def get_stats():
    """查看生成状态统计"""
    db = await get_db()

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM branches WHERE status='generated'")
    total_branches = (await cursor.fetchone())[0]

    cursor = await db.execute("""
        SELECT COUNT(DISTINCT b.id) as cnt
        FROM branches b
        WHERE b.status = 'generated'
        AND EXISTS (SELECT 1 FROM branch_comics bc WHERE bc.branch_id = b.id AND bc.image_path IS NOT NULL)
    """)
    branches_with_comics = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM episodes")
    total_episodes = (await cursor.fetchone())[0]

    cursor = await db.execute("""
        SELECT COUNT(DISTINCT e.id) as cnt
        FROM episodes e
        WHERE EXISTS (SELECT 1 FROM branches b WHERE b.episode_id = e.id AND b.status = 'generated')
    """)
    episodes_with_branches = (await cursor.fetchone())[0]

    return {
        "total_episodes": total_episodes,
        "episodes_with_branches": episodes_with_branches,
        "total_branches": total_branches,
        "branches_with_comics": branches_with_comics,
        "branches_without_comics": total_branches - branches_with_comics,
    }
