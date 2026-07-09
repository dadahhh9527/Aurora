"""
应用级运行时配置：统一从环境变量 / .env 读取，均带合理默认值。
（config_handler 在导入时已负责加载 .env，这里只做读取。）
"""
import os

from utils.config_handler import _load_dotenv  # noqa: F401  确保 .env 已加载

_load_dotenv()


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


# —— 鉴权：设置了 APP_API_KEY 才开启校验；为空则不校验（方便本地开发） ——
APP_API_KEY: str = (os.environ.get("APP_API_KEY") or "").strip()

# —— 限流：单个客户端（IP + 会话）每分钟允许的对话请求数 ——
RATE_LIMIT_PER_MIN: int = _get_int("RATE_LIMIT_PER_MIN", 20)

# —— CORS：允许的来源，逗号分隔；默认 * ——
ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in (os.environ.get("ALLOWED_ORIGINS", "*")).split(",") if o.strip()
]

# —— 记忆持久化：SQLite 文件路径；会话空闲过期时间（分钟） ——
MEMORY_DB_PATH: str = os.environ.get("MEMORY_DB_PATH", "checkpoints.sqlite")
SESSION_TTL_MINUTES: int = _get_int("SESSION_TTL_MINUTES", 180)

# —— 摘要式记忆裁剪：消息数超过 trigger 时触发摘要，保留最近 keep 条 ——
SUMMARY_TRIGGER_MESSAGES: int = _get_int("SUMMARY_TRIGGER_MESSAGES", 30)
SUMMARY_KEEP_MESSAGES: int = _get_int("SUMMARY_KEEP_MESSAGES", 12)

# —— 单次对话内最多的模型调用轮数（防止工具死循环烧钱） ——
MODEL_RUN_LIMIT: int = _get_int("MODEL_RUN_LIMIT", 12)
