"""统一配置中心：所有敏感信息从 .env 读取，禁止硬编码。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 显式指定 .env 位置：无论从哪个目录启动，都能读到同一份配置
load_dotenv(Path(__file__).resolve().parent / ".env")

# ===== 大模型（OpenAI 兼容协议，当前接入：火山引擎方舟）=====
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.3-flash")
# 主模型不可用时自动降级（模型名变更/权限未开通时兜底，可留空）
LLM_MODEL_FALLBACK = os.getenv("LLM_MODEL_FALLBACK", "")

# ===== 企查查开放平台（公司背调的第二数据源，可选）=====
# 注册 https://open.qichacha.cn 申请后填入 .env；未配置时自动跳过该数据源
QCC_API_KEY = os.getenv("QCC_API_KEY", "")
QCC_API_SECRET = os.getenv("QCC_API_SECRET", "")


# MySQL
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "headhunter"),
    "charset": "utf8mb4",
}

# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "AI猎头助手")

# 安全限制
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "100"))
