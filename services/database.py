"""Aurora storage for users, sessions, conversations, memory, and audits."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class User:
    id: str
    business_id: str
    username: str
    role: str
    is_active: bool
    created_at: float


class AppDatabase:
    """Use short-lived SQLite connections instead of sharing them across threads."""

    def __init__(self, path: str):
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    business_id TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    created_by TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                    ON auth_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
                    ON auth_sessions(expires_at);

                CREATE TABLE IF NOT EXISTS conversations (
                    user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at);

                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    memory_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_conversation_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE (user_id, memory_key)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user_updated
                    ON long_term_memories(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    created_at REAL NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created
                    ON audit_logs(created_at DESC);
                """
            )
            user_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "business_id" not in user_columns:
                conn.execute("ALTER TABLE users ADD COLUMN business_id TEXT")
                conn.execute("UPDATE users SET business_id = id WHERE business_id IS NULL")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_business_id ON users(business_id)"
            )
            count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if count == 0:
                conn.execute("INSERT INTO schema_version(version) VALUES (1)")

    @staticmethod
    def _to_user(row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(
            id=row["id"],
            business_id=row["business_id"],
            username=row["username"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            created_at=float(row["created_at"]),
        )

    def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "user",
        created_by: str | None = None,
        business_id: str | None = None,
    ) -> User:
        now = time.time()
        user_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users(id, business_id, username, password_hash, role, is_active,
                                  created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    user_id,
                    business_id or user_id,
                    username,
                    password_hash,
                    role,
                    now,
                    now,
                    created_by,
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._to_user(row)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> User | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._to_user(row)

    def get_user_with_hash(self, username: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()

    def list_users(self) -> list[User]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY username COLLATE NOCASE"
            ).fetchall()
        return [self._to_user(row) for row in rows if row is not None]  # type: ignore[misc]

    def count_users(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        is_active: bool | None = None,
        password_hash: str | None = None,
    ) -> User | None:
        fields: list[str] = []
        values: list[object] = []
        if role is not None:
            fields.append("role = ?")
            values.append(role)
        if is_active is not None:
            fields.append("is_active = ?")
            values.append(int(is_active))
        if password_hash is not None:
            fields.append("password_hash = ?")
            values.append(password_hash)
        if not fields:
            return self.get_user(user_id)
        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(user_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._to_user(row)

    def create_auth_session(
        self, token_hash: str, user_id: str, expires_at: float
    ) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, now, expires_at),
            )

    def user_for_session(self, token_hash: str, now: float | None = None) -> User | None:
        now = now or time.time()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT u.* FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
        return self._to_user(row)

    def revoke_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    def prune_expired_auth_sessions(self, now: float | None = None) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?",
                (now or time.time(),),
            )
        return int(cursor.rowcount)

    def ensure_conversation(
        self, user_id: str, conversation_id: str, thread_id: str
    ) -> None:
        now = time.time()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT thread_id FROM conversations
                WHERE user_id = ? AND conversation_id = ?
                """,
                (user_id, conversation_id),
            ).fetchone()
            if existing and existing["thread_id"] != thread_id:
                raise ValueError("Conversation identity mismatch")
            conn.execute(
                """
                INSERT INTO conversations(user_id, conversation_id, thread_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, conversation_id)
                DO UPDATE SET updated_at = excluded.updated_at
                """,
                (user_id, conversation_id, thread_id, now, now),
            )

    def delete_conversation(self, user_id: str, conversation_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT thread_id FROM conversations
                WHERE user_id = ? AND conversation_id = ?
                """,
                (user_id, conversation_id),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    DELETE FROM conversations
                    WHERE user_id = ? AND conversation_id = ?
                    """,
                    (user_id, conversation_id),
                )
        return row["thread_id"] if row else None

    def conversation_thread(self, user_id: str, conversation_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT thread_id FROM conversations
                WHERE user_id = ? AND conversation_id = ?
                """,
                (user_id, conversation_id),
            ).fetchone()
        return row["thread_id"] if row else None

    def pop_expired_conversations(self, cutoff: float) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT thread_id FROM conversations WHERE updated_at < ?", (cutoff,)
            ).fetchall()
            conn.execute("DELETE FROM conversations WHERE updated_at < ?", (cutoff,))
        return [row["thread_id"] for row in rows]

    def list_memories(self, user_id: str, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, memory_key, category, value, confidence,
                       source_conversation_id, created_at, updated_at
                FROM long_term_memories
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_memory(
        self,
        user_id: str,
        memory_key: str,
        category: str,
        value: str,
        confidence: float,
        source_conversation_id: str,
    ) -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO long_term_memories(
                    id, user_id, memory_key, category, value, confidence,
                    source_conversation_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, memory_key) DO UPDATE SET
                    category = excluded.category,
                    value = excluded.value,
                    confidence = excluded.confidence,
                    source_conversation_id = excluded.source_conversation_id,
                    updated_at = excluded.updated_at
                """,
                (
                    uuid.uuid4().hex,
                    user_id,
                    memory_key,
                    category,
                    value,
                    confidence,
                    source_conversation_id,
                    now,
                    now,
                ),
            )

    def clear_memories(self, user_id: str) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM long_term_memories WHERE user_id = ?", (user_id,)
            )
        return int(cursor.rowcount)

    def audit(
        self,
        actor_user_id: str | None,
        action: str,
        target_type: str,
        target_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(
                    actor_user_id, action, target_type, target_id, created_at, detail
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (actor_user_id, action, target_type, target_id, time.time(), detail),
            )
