"""
配置文件
API 密钥从环境变量或 .env 文件读取
"""

import os

# 尝试从 .env 文件加载（开发环境）
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.isfile(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


# MIMO API 配置
MIMO_API_BASE = os.environ.get("MIMO_API_BASE", "https://token-plan-cn.xiaomimimo.com/v1")
MIMO_MODEL = os.environ.get("MIMO_MODEL", "mimo-v2.5")
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "")

# 火山方舟 API 配置（动态漫生成）
ARK_API_BASE = os.environ.get("ARK_API_BASE", "https://ark.cn-beijing.volces.com/api/v3/images/generations")
ARK_MODEL = os.environ.get("ARK_MODEL", "doubao-seedream-5-0-260128")
ARK_API_KEY = os.environ.get("ARK_API_KEY", "")

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "branch_service.db")

# 静态文件路径（漫画图片存储）
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
COMICS_DIR = os.path.join(STATIC_DIR, "branch_comics")

# 视频文件根目录
VIDEO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "短剧")

# 后端服务端口
SERVER_PORT = 8081

# 分支生成配置
DEFAULT_BRANCH_COUNT = 2       # 系统默认生成分支数
DISPLAY_BRANCH_COUNT = 3       # 前端展示分支数（最高票 1 + 随机 2）

# LLM 审核通过阈值
VALIDATION_SCORE_THRESHOLD = 0.6

# 后台生成任务状态
GENERATION_STATUS = {
    "idle": "idle",
    "generating": "generating",
    "completed": "completed",
    "failed": "failed",
}
