from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from assistant.memory.long_term import LongTermMemory

if TYPE_CHECKING:
    from assistant.llm.client import LlamaCppClient

logger = logging.getLogger("assistant.memory")

EXTRACT_SYSTEM_PROMPT = (
    "You are a memory extraction engine for an AI assistant named Thursday. "
    "Read the latest user/assistant exchange and extract DURABLE personal facts "
    "worth remembering long-term: the user's identity, preferences, relationships, "
    "projects, skills, recurring goals, and concrete factual statements. "
    "Ignore transient requests, tool outputs, small talk, and anything already obvious. "
    "Respond ONLY with compact JSON of the form:\n"
    '{"facts":[{"subject":"","predicate":"","object":""}],'
    '"preferences":[{"key":"","value":""}],'
    '"memories":[{"name":"","content":""}]}\n'
    "If there is nothing durable, respond with {} . Do not include any prose."
)


def _extract_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    clean = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
    return {}


def extract_and_store(
    llm: "LlamaCppClient",
    memory: LongTermMemory,
    user_text: str,
    assistant_text: str,
    max_tokens: int = 128,
) -> int:
    """Ask the LLM to extract durable facts from an exchange and store them.

    Returns the number of items stored. Designed to be run in a background
    thread; all errors are swallowed so it can never break the main response.
    """
    exchange = f"User: {user_text}\nAssistant: {assistant_text[:1000]}"
    messages = [
        {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": exchange},
    ]
    try:
        from assistant.llm.client import ChatMessage

        response = llm.chat(
            [ChatMessage(role=m["role"], content=m["content"]) for m in messages],
            tools=None,
            use_response_format=False,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort background work
        logger.debug("Memory extraction LLM call failed: %s", exc)
        return 0

    data = _extract_json(response.content or "")
    if not data:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    stored = 0

    for fact in data.get("facts", []) or []:
        try:
            subject = str(fact.get("subject", "")).strip()
            predicate = str(fact.get("predicate", "")).strip()
            obj = str(fact.get("object", "")).strip()
            if subject and predicate and obj:
                memory.remember_fact(subject, predicate, obj, now, importance=0.7)
                stored += 1
        except Exception:  # noqa: BLE001
            continue

    for pref in data.get("preferences", []) or []:
        try:
            key = str(pref.get("key", "")).strip()
            value = str(pref.get("value", "")).strip()
            if key and value:
                memory.set_preference(key, value, now)
                stored += 1
        except Exception:  # noqa: BLE001
            continue

    for mem in data.get("memories", []) or []:
        try:
            name = str(mem.get("name", "")).strip()
            content = str(mem.get("content", "")).strip()
            if name and content:
                memory.store_memory(name, content, now, importance=0.7)
                stored += 1
        except Exception:  # noqa: BLE001
            continue

    return stored


def run_extraction_async(
    llm: "LlamaCppClient",
    memory: LongTermMemory,
    user_text: str,
    assistant_text: str,
) -> None:
    """Fire-and-forget extraction on a daemon thread."""
    import threading

    if not user_text or not assistant_text:
        return
    # Cheap heuristic: skip tiny exchanges that rarely contain durable facts.
    if len(user_text) + len(assistant_text) < 40:
        return

    def _worker() -> None:
        # Wait briefly so the main conversation's KV cache slots are freed
        # before this background call competes for them.
        import time
        time.sleep(2.0)
        try:
            extract_and_store(llm, memory, user_text, assistant_text, max_tokens=128)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Background memory extraction error: %s", exc)

    t = threading.Thread(target=_worker, daemon=True, name="memory-extract")
    t.start()
