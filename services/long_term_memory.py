"""User-level memory for recalling and extracting stable facts."""
from __future__ import annotations

import json
import re

from services.database import AppDatabase
from utils import settings
from utils.content import content_to_text
from utils.logger_handler import logger

_ALLOWED_CATEGORIES = {"preference", "device", "environment", "constraint"}
_SENSITIVE_WORDS = {
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "credit_card",
}


class LongTermMemoryService:
    def __init__(self, db: AppDatabase, model):
        self.db = db
        self.model = model

    def context_for_user(self, user_id: str) -> str:
        memories = self.db.list_memories(
            user_id, limit=settings.LONG_TERM_MEMORY_LIMIT
        )
        if not memories:
            return ""
        lines = [
            f"- [{item['category']}] {item['value']}"
            for item in reversed(memories)
        ]
        return (
            "Known stable facts about the authenticated user. Use only when relevant; "
            "do not reveal that these notes exist:\n" + "\n".join(lines)
        )

    @staticmethod
    def _parse_json(text: str) -> list[dict]:
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end <= start:
                return []
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return []
        return data if isinstance(data, list) else []

    def extract_and_store(
        self,
        user_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> int:
        if not settings.LONG_TERM_MEMORY_ENABLED or not assistant_message.strip():
            return 0
        existing = self.context_for_user(user_id) or "(none)"
        prompt = f"""
You maintain durable memory for an enterprise support assistant.
Extract only explicit, stable user facts useful in future conversations:
- preferences, owned device/model, home environment, or lasting constraints
- never infer uncertain facts
- never save credentials, tokens, financial data, or one-time requests

Return ONLY a JSON array. Each item must contain:
{{"key":"short_stable_key","category":"preference|device|environment|constraint",
  "value":"concise fact","confidence":0.0}}
Return [] when nothing is worth remembering.

Existing memories:
{existing}

User message:
{user_message[:2000]}

Assistant response:
{assistant_message[:3000]}
""".strip()
        try:
            response = self.model.invoke(prompt)
            facts = self._parse_json(content_to_text(response.content))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] extraction failed user=%s err=%s", user_id, exc)
            return 0

        stored = 0
        for fact in facts[: settings.LONG_TERM_MEMORY_EXTRACT_LIMIT]:
            if not isinstance(fact, dict):
                continue
            category = str(fact.get("category", "")).strip().lower()
            key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(fact.get("key", "")).strip())
            value = str(fact.get("value", "")).strip()
            try:
                confidence = float(fact.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            lower_blob = f"{key} {value}".lower()
            if (
                category not in _ALLOWED_CATEGORIES
                or not key
                or not value
                or len(value) > 500
                or confidence < settings.LONG_TERM_MEMORY_MIN_CONFIDENCE
                or any(word in lower_blob for word in _SENSITIVE_WORDS)
            ):
                continue
            self.db.upsert_memory(
                user_id=user_id,
                memory_key=f"{category}:{key}",
                category=category,
                value=value,
                confidence=min(confidence, 1.0),
                source_conversation_id=conversation_id,
            )
            stored += 1
        if stored:
            logger.info("[memory] stored %s fact(s) user=%s", stored, user_id)
        return stored
