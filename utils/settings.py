"""
Application runtime settings loaded from environment variables or .env.
config_handler loads .env before these values are read.
"""
import os

from utils.config_handler import _load_dotenv

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


def _get_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Runtime mode: Debug keeps the login-free demo; production requires local accounts.
APP_DEBUG: bool = _get_bool("APP_DEBUG", False)

# Application database and local login sessions.
APP_DB_PATH: str = os.environ.get("APP_DB_PATH", "aurora.sqlite")
AUTH_SESSION_HOURS: int = _get_int("AUTH_SESSION_HOURS", 12)
MIN_PASSWORD_LENGTH: int = _get_int("MIN_PASSWORD_LENGTH", 10)
COOKIE_SECURE: bool = _get_bool("COOKIE_SECURE", False)

# Per-client rate limits.
RATE_LIMIT_PER_MIN: int = _get_int("RATE_LIMIT_PER_MIN", 20)
LOGIN_RATE_LIMIT_PER_MIN: int = _get_int("LOGIN_RATE_LIMIT_PER_MIN", 10)

# Comma-separated CORS origins.
ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000",
    ).split(",")
    if o.strip()
]

# Conversation checkpoint path and idle expiration.
MEMORY_DB_PATH: str = os.environ.get("MEMORY_DB_PATH", "checkpoints.sqlite")
SESSION_TTL_MINUTES: int = _get_int("SESSION_TTL_MINUTES", 180)

# Summarize history after the trigger and retain the most recent messages.
SUMMARY_TRIGGER_MESSAGES: int = _get_int("SUMMARY_TRIGGER_MESSAGES", 30)
SUMMARY_KEEP_MESSAGES: int = _get_int("SUMMARY_KEEP_MESSAGES", 12)

# Maximum model calls per turn to prevent runaway tool loops.
MODEL_RUN_LIMIT: int = _get_int("MODEL_RUN_LIMIT", 12)

# Long-term memory stores stable user facts, not full conversations.
LONG_TERM_MEMORY_ENABLED: bool = _get_bool("LONG_TERM_MEMORY_ENABLED", True)
LONG_TERM_MEMORY_LIMIT: int = _get_int("LONG_TERM_MEMORY_LIMIT", 30)
LONG_TERM_MEMORY_EXTRACT_LIMIT: int = _get_int("LONG_TERM_MEMORY_EXTRACT_LIMIT", 5)
LONG_TERM_MEMORY_MIN_CONFIDENCE: float = _get_float(
    "LONG_TERM_MEMORY_MIN_CONFIDENCE", 0.8
)

# Local knowledge scans run only during the web application lifespan.
KB_SCAN_ENABLED: bool = _get_bool("KB_SCAN_ENABLED", True)
KB_SCAN_INTERVAL_SECONDS: int = _get_int("KB_SCAN_INTERVAL_SECONDS", 600)
