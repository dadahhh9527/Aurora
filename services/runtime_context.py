"""Request-scoped identity context used by agent tools."""
from contextvars import ContextVar, Token

_current_user_id: ContextVar[str | None] = ContextVar(
    "aurora_current_user_id", default=None
)


def set_current_user_id(user_id: str) -> Token:
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> str:
    user_id = _current_user_id.get()
    if not user_id:
        raise RuntimeError("No authenticated user is available in the agent context.")
    return user_id
