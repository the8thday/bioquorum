"""Record and replay model calls (ARCHITECTURE.md §6.8: models/ tests never call a model).

Keys are computed from the canonical request (model spec, messages, tool definitions)
with every ULID replaced by a placeholder, because run IDs and call IDs differ between
runs. Responses are stored in canonical form, so cassettes survive Pydantic AI upgrades.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from bioquorum.core.hashing import canonical_json, sha256_bytes
from bioquorum.core.messages import Message
from bioquorum.policy.secrets import recorded_message

_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
FORMAT = 1


class CassetteMode(StrEnum):
    REPLAY = "replay"  # only recorded responses; a miss is an error
    RECORD = "record"  # call the model, store the response
    LIVE = "live"  # call the model, store nothing


class CassetteMissError(LookupError):
    pass


def request_key(spec: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> str:
    payload = {
        "spec": spec,
        "messages": [m.model_dump(mode="json") for m in messages],
        "tools": list(tools),
    }
    text = _ULID.sub("<ID>", canonical_json(payload).decode("utf-8"))
    return sha256_bytes(text.encode("utf-8"))


class ModelCassette:
    def __init__(self, path: str | Path, mode: CassetteMode = CassetteMode.REPLAY) -> None:
        self.path = Path(path)
        self.mode = mode
        self._entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"{self.path}: unsupported model cassette format")
            self._entries = data["entries"]

    def lookup(self, key: str) -> tuple[Message, dict[str, int]]:
        entry = self._entries.get(key)
        if entry is None:
            raise CassetteMissError(
                f"no recorded model response in {self.path.name} for this request; "
                "re-record with --record"
            )
        return Message.model_validate(entry["message"]), dict(entry["usage"])

    def store(self, key: str, spec: str, message: Message, usage: dict[str, int]) -> None:
        self._entries[key] = {
            "spec": spec,
            "message": recorded_message(message).model_dump(mode="json"),
            "usage": usage,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"format": FORMAT, "entries": self._entries}
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._entries)
