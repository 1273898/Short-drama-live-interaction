"""
分支 API 路由
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, Query

from database import get_db, is_generating
from models import (
    BranchListResponse, BranchInfo, ComicPanel,
    SubmitBranchRequest, SubmitBranchResponse,
    VoteRequest, VoteResponse, ComicListResponse
)
from services.llm_validator import validate_user_branch
from services.background_worker import generate_branches_for_episode, generate_single_user_branch
from config import DISPLAY_BRANCH_COUNT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/episodes/{episode_id}/branches", response_model=BranchListResponse)
async def get_branches(episode_id: int, user_id: str = Query(default="anonymous")):
    """
    获取剧集的分支列表
    返回 3 个分支：点赞最高的 1 个 + 随机 2 个
    """
    db = await get_db()

    # 获取所有已生成的分支
    cursor = await db.execute(
        """SELECT b.*, d.title as drama_title
           FROM branches b
           JOIN episodes e ON b.episode_id = e.id
           JOIN dramas d ON e.drama_id = d.id
           WHERE b.episode_id = ? AND b.status = 'generated'
           ORDER BY b.vote_count DESC""",
        (episode_id,)
    )
    all_branches = await cursor.fetchall()

    if not all_branches:
        # 没有分支，检查是否正在生成中
        generating = is_generating(episode_id)
        return BranchListResponse(
            episode_id=episode_id,
            branches=[],
            has_branches=False,
            is_generating=generating,
        )

    # 选择展示的分支：最高票 1 个 + 随机 2 个
    selected = []
    if len(all_branches) > 0:
        selected.append(all_branches[0])  # 最高票

    if len(all_branches) > DISPLAY_BRANCH_COUNT:
        # 从剩余中随机选 2 个
        import random
        remaining = [b for b in all_branches[1:] if b["id"] != all_branches[0]["id"]]
        random.shuffle(remaining)
        selected.extend(remaining[:DISPLAY_BRANCH_COUNT - 1])
    else:
        selected = list(all_branches)

    # 限制为 DISPLAY_BRANCH_COUNT 个
    selected = selected[:DISPLAY_BRANCH_COUNT]

    # 构建响应
    branch_infos = []
    for branch in selected:
        # 获取漫画
        cursor = await db.execute(
            "SELECT * FROM branch_comics WHERE branch_id = ? ORDER BY panel_number",
            (branch["id"],)
        )
        comics = await cursor.fetchall()

        # 检查用户是否已投票
        cursor = await db.execute(
            "SELECT 1 FROM branch_votes WHERE branch_id = ? AND user_id = ?",
            (branch["id"], user_id)
        )
        has_voted = (await cursor.fetchone()) is not None

        branch_infos.append(BranchInfo(
            id=branch["id"],
            title=branch["title"],
            description=branch["description"],
            tone=branch["tone"] or "",
            preview=branch["preview"] or "",
            vote_count=branch["vote_count"],
            source=branch["source"] or "auto",
            user_has_voted=has_voted,
            comics=[
                ComicPanel(
                    panel_number=c["panel_number"],
                    image_url=f"/static/{c['image_path']}" if c["image_path"] else "",
                    description=c["description"] or "",
                )
                for c in comics
            ],
        ))

    return BranchListResponse(
        episode_id=episode_id,
        branches=branch_infos,
        has_branches=True,
        is_generating=is_generating(episode_id),
    )


@router.post("/episodes/{episode_id}/branches", response_model=SubmitBranchResponse)
async def submit_branch(episode_id: int, request: SubmitBranchRequest):
    """
    用户提交自定义分支
    先通过 LLM 审核，通过后触发漫画生成
    """
    db = await get_db()

    # 获取剧集信息
    cursor = await db.execute(
        "SELECT e.*, d.title as drama_title FROM episodes e JOIN dramas d ON e.drama_id = d.id WHERE e.id = ?",
        (episode_id,)
    )
    episode = await cursor.fetchone()
    if not episode:
        return SubmitBranchResponse(branch_id=0, status="error", rejection_reason="剧集不存在")

    # 获取已有分支（用于去重检查）
    cursor = await db.execute(
        "SELECT title, description, tone FROM branches WHERE episode_id = ? AND status = 'generated'",
        (episode_id,)
    )
    existing = await cursor.fetchall()
    existing_branches = [dict(b) for b in existing]

    # 获取剧情概要
    cursor = await db.execute(
        "SELECT summary FROM episode_metadata WHERE episode_id = ?", (episode_id,)
    )
    meta = await cursor.fetchone()
    episode_summary = meta["summary"] if meta and meta["summary"] else f"第{episode['episode_number']}集"

    # LLM 审核
    loop = asyncio.get_event_loop()
    validation = await loop.run_in_executor(
        None,
        lambda: validate_user_branch(
            drama_name=episode["drama_title"],
            episode_summary=episode_summary,
            user_title=request.title,
            user_description=request.description,
            user_tone=request.tone,
            user_preview=request.preview,
            existing_branches=existing_branches,
        )
    )

    if not validation.get("valid", False):
        # 审核不通过
        cursor = await db.execute(
            """INSERT INTO branches (episode_id, title, description, tone, preview, source, user_id, status, rejection_reason)
               VALUES (?, ?, ?, ?, ?, 'user', ?, 'rejected', ?)""",
            (episode_id, request.title, request.description, request.tone, request.preview,
             request.user_id, validation.get("reason", "审核未通过"))
        )
        await db.commit()
        return SubmitBranchResponse(
            branch_id=cursor.lastrowid,
            status="rejected",
            rejection_reason=validation.get("reason", "审核未通过"),
        )

    # 审核通过，存入分支
    cursor = await db.execute(
        """INSERT INTO branches (episode_id, title, description, tone, preview, source, user_id, status)
           VALUES (?, ?, ?, ?, ?, 'user', ?, 'validated')""",
        (episode_id, request.title, request.description, request.tone, request.preview, request.user_id)
    )
    branch_id = cursor.lastrowid
    await db.commit()

    # 后台触发漫画生成
    asyncio.create_task(_generate_user_branch_comics(branch_id))

    return SubmitBranchResponse(branch_id=branch_id, status="validated")


async def _generate_user_branch_comics(branch_id: int):
    """后台为用户分支生成漫画"""
    try:
        await generate_single_user_branch(branch_id)
    except Exception as e:
        logger.error(f"用户分支漫画生成异常: {e}")


@router.post("/branches/{branch_id}/vote", response_model=VoteResponse)
async def vote_branch(branch_id: int, request: VoteRequest):
    """点赞分支"""
    db = await get_db()

    # 检查是否已投票
    cursor = await db.execute(
        "SELECT 1 FROM branch_votes WHERE branch_id = ? AND user_id = ?",
        (branch_id, request.user_id)
    )
    if await cursor.fetchone():
        # 已投票，返回当前计数
        cursor = await db.execute("SELECT vote_count FROM branches WHERE id = ?", (branch_id,))
        branch = await cursor.fetchone()
        return VoteResponse(
            branch_id=branch_id,
            vote_count=branch["vote_count"] if branch else 0,
            already_voted=True,
        )

    # 新增投票
    await db.execute(
        "INSERT INTO branch_votes (branch_id, user_id) VALUES (?, ?)",
        (branch_id, request.user_id)
    )
    await db.execute(
        "UPDATE branches SET vote_count = vote_count + 1 WHERE id = ?",
        (branch_id,)
    )
    await db.commit()

    cursor = await db.execute("SELECT vote_count FROM branches WHERE id = ?", (branch_id,))
    branch = await cursor.fetchone()

    return VoteResponse(
        branch_id=branch_id,
        vote_count=branch["vote_count"] if branch else 0,
        already_voted=False,
    )


@router.get("/branches/{branch_id}/comics", response_model=ComicListResponse)
async def get_branch_comics(branch_id: int):
    """获取分支的漫画详情"""
    db = await get_db()

    cursor = await db.execute(
        "SELECT * FROM branch_comics WHERE branch_id = ? ORDER BY panel_number",
        (branch_id,)
    )
    comics = await cursor.fetchall()

    return ComicListResponse(
        branch_id=branch_id,
        comics=[
            ComicPanel(
                panel_number=c["panel_number"],
                image_url=f"/static/{c['image_path']}" if c["image_path"] else "",
                description=c["description"] or "",
            )
            for c in comics
        ],
    )
