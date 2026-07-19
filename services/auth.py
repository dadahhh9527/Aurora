"""Local account authentication and request-scoped user context."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from services.database import AppDatabase, User
from utils import settings

SESSION_COOKIE = "aurora_session"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    if len(password) < settings.MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {settings.MIN_PASSWORD_LENGTH} characters."
        )
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}"
        f"${salt.hex()}${derived.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected_hex)),
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthSession:
    token: str
    expires_at: float


class AuthService:
    def __init__(self, db: AppDatabase):
        self.db = db
        self._dummy_hash = hash_password(secrets.token_urlsafe(24))

    def authenticate(self, username: str, password: str) -> User | None:
        row = self.db.get_user_with_hash(username.strip())
        if not row:
            # Use the same verification cost to reduce username-enumeration timing leaks.
            verify_password(password, self._dummy_hash)
            return None
        password_valid = verify_password(password, row["password_hash"])
        if not password_valid or not row["is_active"]:
            return None
        return self.db.get_user(row["id"])

    def create_session(self, user_id: str) -> AuthSession:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + settings.AUTH_SESSION_HOURS * 3600
        self.db.create_auth_session(_token_hash(token), user_id, expires_at)
        return AuthSession(token=token, expires_at=expires_at)

    def user_for_token(self, token: str | None) -> User | None:
        if not token:
            return None
        return self.db.user_for_session(_token_hash(token))

    def revoke(self, token: str | None) -> None:
        if token:
            self.db.revoke_session(_token_hash(token))


def _debug_user() -> User:
    return User(
        id="debug-admin",
        business_id="debug-admin",
        username="debug",
        role="admin",
        is_active=True,
        created_at=0,
    )


def require_user(request: Request) -> User:
    if settings.APP_DEBUG:
        return _debug_user()
    service: AuthService = request.app.state.auth
    user = service.user_for_token(request.cookies.get(SESSION_COOKIE))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user


def require_admin(request: Request) -> User:
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return user
