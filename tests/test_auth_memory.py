import time
import sqlite3

from services.auth import AuthService, hash_password, verify_password
from services.database import AppDatabase
from services.long_term_memory import LongTermMemoryService
from services.runtime_context import (
    get_current_user_id,
    reset_current_user_id,
    set_current_user_id,
)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_user_auth_session_and_revocation(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    user = db.create_user("alice", hash_password("long-enough-password"))
    auth = AuthService(db)

    assert auth.authenticate("ALICE", "long-enough-password") == user
    assert auth.authenticate("alice", "incorrect") is None

    session = auth.create_session(user.id)
    assert auth.user_for_token(session.token) == user
    auth.revoke(session.token)
    assert auth.user_for_token(session.token) is None


def test_business_user_id_is_unique(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    db.create_user(
        "alice",
        hash_password("long-enough-password"),
        business_id="1001",
    )
    try:
        db.create_user(
            "bob",
            hash_password("long-enough-password"),
            business_id="1001",
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate business user ID was accepted")


def test_disabled_user_cannot_reuse_session(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    user = db.create_user("bob", hash_password("long-enough-password"))
    auth = AuthService(db)
    session = auth.create_session(user.id)

    db.update_user(user.id, is_active=False)
    assert auth.user_for_token(session.token) is None


def test_expired_auth_sessions_are_pruned(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    user = db.create_user("carol", hash_password("long-enough-password"))
    db.create_auth_session("expired-token-hash", user.id, time.time() - 1)
    assert db.prune_expired_auth_sessions() == 1


def test_conversations_are_scoped_by_user(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    db.ensure_conversation("user-a", "conversation-1", "v2:user-a:conversation-1")
    db.ensure_conversation("user-b", "conversation-1", "v2:user-b:conversation-1")
    assert (
        db.conversation_thread("user-a", "conversation-1")
        == "v2:user-a:conversation-1"
    )
    try:
        db.ensure_conversation("user-a", "conversation-1", "different-thread")
    except ValueError:
        pass
    else:
        raise AssertionError("conversation identity mismatch was accepted")

    assert (
        db.delete_conversation("user-a", "conversation-1")
        == "v2:user-a:conversation-1"
    )
    assert (
        db.delete_conversation("user-b", "conversation-1")
        == "v2:user-b:conversation-1"
    )


def test_long_term_memories_are_user_scoped_and_upserted(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    db.upsert_memory(
        "user-a", "device:model", "device", "Owns model A", 0.9, "conversation-1"
    )
    db.upsert_memory(
        "user-a", "device:model", "device", "Owns model B", 0.95, "conversation-2"
    )
    db.upsert_memory(
        "user-b", "device:model", "device", "Owns model C", 0.9, "conversation-1"
    )

    memories_a = db.list_memories("user-a")
    assert len(memories_a) == 1
    assert memories_a[0]["value"] == "Owns model B"
    assert db.list_memories("user-b")[0]["value"] == "Owns model C"
    assert db.clear_memories("user-a") == 1
    assert db.list_memories("user-a") == []
    assert len(db.list_memories("user-b")) == 1


def test_expired_conversations_are_popped(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    db.ensure_conversation("user-a", "conversation-1", "thread-1")
    assert db.pop_expired_conversations(time.time() + 1) == ["thread-1"]


class _Response:
    def __init__(self, content):
        self.content = content


class _MemoryModel:
    def invoke(self, _prompt):
        return _Response(
            """```json
            [
              {"key":"model","category":"device","value":"Owns an Aurora X1","confidence":0.95},
              {"key":"api_key","category":"preference","value":"secret token abc","confidence":1.0},
              {"key":"guess","category":"environment","value":"May have pets","confidence":0.4}
            ]
            ```"""
        )


def test_memory_extraction_filters_sensitive_and_uncertain_facts(tmp_path):
    db = AppDatabase(str(tmp_path / "app.sqlite"))
    service = LongTermMemoryService(db, _MemoryModel())
    stored = service.extract_and_store(
        "user-a",
        "conversation-1",
        "I own an Aurora X1.",
        "Thanks, I will remember your model.",
    )
    assert stored == 1
    assert db.list_memories("user-a")[0]["value"] == "Owns an Aurora X1"


def test_request_user_context_is_reset():
    token = set_current_user_id("business-1001")
    assert get_current_user_id() == "business-1001"
    reset_current_user_id(token)
    try:
        get_current_user_id()
    except RuntimeError:
        pass
    else:
        raise AssertionError("user context leaked after reset")
