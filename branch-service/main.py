"""
剧情分支生成服务 - FastAPI 入口
端口 8081
启动时自动为所有剧集预生成分支
"""

import logging
import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from database import init_db, close_db, get_db
from config import SERVER_PORT, STATIC_DIR
from routers import branches, admin
from services.background_worker import generate_branches_for_episode

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def pre_generate_all():
    """启动时为所有剧集串行预生成分支（避免并发触发限流）"""
    try:
        db = await get_db()
        cursor = await db.execute("""
            SELECT e.id, e.episode_number, d.title as drama_title
            FROM episodes e
            JOIN dramas d ON e.drama_id = d.id
            WHERE NOT EXISTS (
                SELECT 1 FROM branches b WHERE b.episode_id = e.id AND b.status = 'generated'
            )
            ORDER BY e.id
        """)
        episodes = await cursor.fetchall()

        if not episodes:
            logger.info("所有剧集已有分支，跳过预生成")
            return

        logger.info(f"发现 {len(episodes)} 个剧集需要生成分支，串行预生成中...")
        for ep in episodes:
            logger.info(f"  预生成: {ep['drama_title']} 第{ep['episode_number']}集 (ID: {ep['id']})")
            try:
                await generate_branches_for_episode(ep['id'])
                logger.info(f"  完成: {ep['drama_title']} 第{ep['episode_number']}集")
            except Exception as e:
                logger.error(f"  失败: {ep['drama_title']} 第{ep['episode_number']}集 - {e}")
            # 每个剧集之间等待，避免触发限流
            await asyncio.sleep(3)

    except Exception as e:
        logger.error(f"预生成检查失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("正在初始化数据库...")
    await init_db()
    # 确保静态文件目录存在
    os.makedirs(os.path.join(STATIC_DIR, "branch_comics"), exist_ok=True)
    logger.info("数据库初始化完成")

    # 启动后台预生成任务
    asyncio.create_task(pre_generate_all())

    logger.info(f"服务启动完成，端口 {SERVER_PORT}")
    yield
    logger.info("正在关闭服务...")
    await close_db()


app = FastAPI(
    title="短剧剧情分支服务",
    description="为短剧互动应用提供剧情分支生成、漫画生成、投票等功能",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件挂载
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 注册路由
app.include_router(branches.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"service": "短剧剧情分支服务", "version": "1.0.0", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
