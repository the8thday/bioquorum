"""Keep secrets out of events, logs and exception text (principle 10, ARCHITECTURE.md §6.7).

Resolved secret values are registered here when a provider client is built; ``redact`` is
applied to every error string before it is written to the event log.
"""

from __future__ import annotations

import re

_KNOWN: set[str] = set()
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI / Anthropic / DeepSeek style keys
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)([^\s'\"&]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{12,})"),
]
MASK = "***"


def register_secret(value: str) -> None:
    if len(value) >= 8:
        _KNOWN.add(value)


def redact(text: str) -> str:
    for value in sorted(_KNOWN, key=len, reverse=True):
        text = text.replace(value, MASK)
    text = _PATTERNS[0].sub(MASK, text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{MASK}", text)
    return text
