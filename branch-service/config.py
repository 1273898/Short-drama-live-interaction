"""
配置文件
API 密钥、数据库路径、常量
"""

import os

# MIMO API 配置
MIMO_API_BASE = "https://token-plan-cn.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"
MIMO_API_KEY = "tp-ck89knj2pmg9524gu497iklyj5gaoqbpdxh6ov8cjpa321tm"

# 火山方舟 API 配置（动态漫生成）
ARK_API_BASE = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
ARK_MODEL = "doubao-seedream-5-0-260128"
ARK_API_KEY = "ark-cea3f6ca-83e9-4eb4-87ef-7d51a21ff4ac-ea3ea"

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
