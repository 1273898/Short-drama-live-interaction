"""
SQLite 数据库初始化和操作
"""

import aiosqlite
import os
from config import DB_PATH

# 全局数据库连接
_db: aiosqlite.Connection = None

# 正在生成中的剧集（内存状态）
_generating_episodes: set = set()


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def init_db():
    """初始化数据库表结构"""
    db = await get_db()

    await db.executescript("""
        CREATE TABLE IF NOT EXISTS dramas (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            cover_url TEXT
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drama_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            episode_number INTEGER NOT NULL,
            video_path TEXT NOT NULL,
            FOREIGN KEY (drama_id) REFERENCES dramas(id)
        );

        CREATE TABLE IF NOT EXISTS branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            tone TEXT,
            preview TEXT,
            vote_count INTEGER DEFAULT 0,
            source TEXT DEFAULT 'auto',
            user_id TEXT,
            status TEXT DEFAULT 'generated',
            rejection_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (episode_id) REFERENCES episodes(id)
        );

        CREATE TABLE IF NOT EXISTS branch_comics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            panel_number INTEGER NOT NULL,
            image_path TEXT,
            description TEXT,
            FOREIGN KEY (branch_id) REFERENCES branches(id),
            UNIQUE(branch_id, panel_number)
        );

        CREATE TABLE IF NOT EXISTS branch_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (branch_id) REFERENCES branches(id),
            UNIQUE(branch_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS episode_metadata (
            episode_id INTEGER PRIMARY KEY,
            summary TEXT,
            last_subtitle TEXT,
            keyframe_path TEXT,
            analysis TEXT,
            FOREIGN KEY (episode_id) REFERENCES episodes(id)
        );

        CREATE INDEX IF NOT EXISTS idx_branches_episode ON branches(episode_id);
        CREATE INDEX IF NOT EXISTS idx_branch_comics_branch ON branch_comics(branch_id);
        CREATE INDEX IF NOT EXISTS idx_branch_votes_branch ON branch_votes(branch_id);
    """)

    await db.commit()
    await seed_data(db)


async def seed_data(db: aiosqlite.Connection):
    """填充初始数据（与 DramaRepository.kt 保持一致）"""
    # 检查是否已有数据
    cursor = await db.execute("SELECT COUNT(*) FROM dramas")
    row = await cursor.fetchone()
    if row[0] > 0:
        return

    # 插入剧集
    await db.execute(
        "INSERT INTO dramas (id, title, description, cover_url) VALUES (?, ?, ?, ?)",
        (1, "北派寻宝笔记", "神秘的北派寻宝之旅，探寻失落的宝藏", "")
    )
    await db.execute(
        "INSERT INTO dramas (id, title, description, cover_url) VALUES (?, ?, ?, ?)",
        (2, "天下第一纨绔", "纨绔子弟逆袭成长的热血故事", "")
    )

    # 插入剧集的集数
    for num in range(63, 73):
        await db.execute(
            "INSERT INTO episodes (drama_id, title, episode_number, video_path) VALUES (?, ?, ?, ?)",
            (1, f"第{num}集", num, f"北派寻宝笔记/第{num}集.mp4")
        )

    for num in range(1, 11):
        await db.execute(
            "INSERT INTO episodes (drama_id, title, episode_number, video_path) VALUES (?, ?, ?, ?)",
            (2, f"第{num}集", num, f"天下第一纨绔/第{num}集.mp4")
        )

    await db.commit()


def is_generating(episode_id: int) -> bool:
    return episode_id in _generating_episodes


def set_generating(episode_id: int, generating: bool):
    if generating:
        _generating_episodes.add(episode_id)
    else:
        _generating_episodes.discard(episode_id)
