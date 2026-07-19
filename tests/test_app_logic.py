"""Pure application-level identity tests."""
from app import _debug_chat_user


def test_debug_conversations_share_memory_identity_but_keep_stable_business_ids():
    first = _debug_chat_user("conversation-alpha")
    repeated = _debug_chat_user("conversation-alpha")
    another = _debug_chat_user("conversation-beta")

    assert first.id == repeated.id == another.id == "debug-admin"
    assert first.business_id == repeated.business_id
    assert 1001 <= int(first.business_id) <= 1010
    assert 1001 <= int(another.business_id) <= 1010
