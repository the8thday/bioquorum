"""Ask the reviewer model for JSON and parse it robustly (fences, prose around it)."""

from __future__ import annotations

import json
import re
from typing import Any

from bioquorum.core.enums import Classification, ModelRole
from bioquorum.core.messages import Message
from bioquorum.models.client import ModelClient


class ReviewModelError(RuntimeError):
    pass


def parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        value = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if not m:
            raise ReviewModelError("reviewer did not return JSON") from None
        value = json.loads(m.group(0))
    if not isinstance(value, dict):
        raise ReviewModelError("reviewer returned JSON that is not an object")
    return value


async def ask_json(
    client: ModelClient,
    system: str,
    user: str,
    *,
    session_id: str,
    classification: Classification,
    causation_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Returns (parsed JSON, ``provider:model`` that answered)."""
    completion = await client.complete(
        ModelRole.REVIEWER,
        [Message.system(system), Message.user(user + "\n\nReturn only a JSON object.")],
        session_id=session_id,
        classification=classification,
        causation_id=causation_id,
    )
    return parse_json(completion.message.text()), str(completion.route.spec)
