from __future__ import annotations

import json
import os
from typing import Any


RECENT_MESSAGES_TTL_SECONDS = int(os.getenv("CHAT_RECENT_MESSAGES_TTL_SECONDS", "86400"))
RISK_STATE_TTL_SECONDS = int(os.getenv("CHAT_RISK_STATE_TTL_SECONDS", "86400"))
REQUEST_TTL_SECONDS = int(os.getenv("CHAT_REQUEST_TTL_SECONDS", "600"))
SUMMARY_TTL_SECONDS = int(os.getenv("CHAT_SUMMARY_TTL_SECONDS", "604800"))
RECENT_MESSAGES_LIMIT = int(os.getenv("CHAT_RECENT_MESSAGES_LIMIT", "10"))


class RedisContextCache:
    def __init__(self) -> None:
        self.client = None
        redis_url = os.getenv("REDIS_URL", "")
        if not redis_url:
            return
        try:
            import redis  # type: ignore

            self.client = redis.Redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
        except Exception:
            self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _key(self, template: str, value: str) -> str:
        return template.format(value)

    def push_recent_message(self, session_id: str, message: dict[str, Any]) -> None:
        if not self.client:
            return
        key = self._key("chat:session:{}:recent_messages", session_id)
        self.client.rpush(key, json.dumps(message, ensure_ascii=False))
        self.client.ltrim(key, -RECENT_MESSAGES_LIMIT, -1)
        self.client.expire(key, RECENT_MESSAGES_TTL_SECONDS)

    def get_recent_messages(self, session_id: str) -> list[dict]:
        if not self.client:
            return []
        key = self._key("chat:session:{}:recent_messages", session_id)
        values = self.client.lrange(key, 0, -1)
        messages = []
        for value in values:
            try:
                messages.append(json.loads(value))
            except json.JSONDecodeError:
                continue
        return messages

    def set_summary(self, session_id: str, summary: str) -> None:
        if not self.client:
            return
        key = self._key("chat:session:{}:summary", session_id)
        self.client.setex(key, SUMMARY_TTL_SECONDS, summary)

    def get_summary(self, session_id: str) -> str:
        if not self.client:
            return ""
        key = self._key("chat:session:{}:summary", session_id)
        return self.client.get(key) or ""

    def set_facts(self, session_id: str, facts: dict[str, str]) -> None:
        if not self.client or not facts:
            return
        key = self._key("chat:session:{}:facts", session_id)
        self.client.setex(key, SUMMARY_TTL_SECONDS, json.dumps(facts, ensure_ascii=False))

    def get_facts(self, session_id: str) -> dict[str, str]:
        if not self.client:
            return {}
        key = self._key("chat:session:{}:facts", session_id)
        raw_value = self.client.get(key)
        if not raw_value:
            return {}
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return {}

    def set_risk_state(self, session_id: str, risk_state: dict[str, Any]) -> None:
        if not self.client:
            return
        key = self._key("chat:session:{}:risk_state", session_id)
        self.client.setex(key, RISK_STATE_TTL_SECONDS, json.dumps(risk_state, ensure_ascii=False))

    def cache_intent_analysis(self, request_id: str, intent_analysis: dict[str, Any]) -> None:
        if not self.client:
            return
        key = self._key("chat:req:{}", request_id)
        self.client.setex(key, REQUEST_TTL_SECONDS, json.dumps(intent_analysis, ensure_ascii=False))


_CACHE: RedisContextCache | None = None


def get_redis_context_cache() -> RedisContextCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = RedisContextCache()
    return _CACHE
