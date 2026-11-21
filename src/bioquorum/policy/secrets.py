"""Keep secrets out of events, logs and exception text (principle 10, ARCHITECTURE.md §6.7).

Resolved secret values are registered here when a provider client is built; ``redact`` is
applied to every error string before it is written to the event log.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from bioquorum.core.messages import Message, TextPart, ToolCallPart

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


_URL = re.compile(r"""https?://[^\s<>"'`]+""", re.I)
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|token|signature|credential|authorization|api[_-]?key|secret|^sig$)", re.I
)


def public_url(value: str, *, strip_query: bool = True) -> str:
    """Omit bearer material from signed locators while retaining public dataset identifiers."""
    try:
        parts = urlsplit(value)
        signed = (strip_query and bool(parts.query)) or any(
            _SENSITIVE_KEY.search(key) for key, _ in parse_qsl(parts.query)
        )
        if parts.username is None and parts.password is None and not signed and not parts.fragment:
            return value
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc.rsplit("@", 1)[-1],
                parts.path,
                "" if signed else parts.query,
                "",
            )
        )
    except ValueError:
        return "[invalid URL]"


def download_text(text: str) -> str:
    return _URL.sub(lambda match: public_url(match[0]), redact(text))


def display_path(value: str, root: Path | None = None) -> str:
    path = Path(value)
    if value.startswith("~"):
        return "[external]/" + path.name
    if not path.is_absolute() and ".." not in path.parts:
        return value
    if root and path.is_relative_to(root) and ".." not in path.parts:
        return str(path.relative_to(root))
    return "[external]/" + path.name


def recorded_args(tool: str, args: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: MASK if _SENSITIVE_KEY.search(k) else clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return redact(value) if isinstance(value, str) else value

    recorded = clean(args)
    if tool == "download_file" and isinstance(url := recorded.get("url"), str):
        recorded["url"] = public_url(url)
    if tool in ("read_file", "list_files") and isinstance(path := recorded.get("path"), str):
        recorded["path"] = display_path(path, root)
    if tool in ("python_execute", "r_execute"):
        for field in ("input_files", "output_files"):
            if isinstance(paths := recorded.get(field), list):
                recorded[field] = [
                    display_path(path, root) for path in paths if isinstance(path, str)
                ]
    return recorded


def recorded_message(message: Message, root: Path | None = None) -> Message:
    parts = []
    for part in message.parts:
        if isinstance(part, ToolCallPart):
            part = part.model_copy(update={"args": recorded_args(part.name, part.args, root)})
        elif isinstance(part, TextPart):
            part = part.model_copy(update={"text": redact(part.text)})
        parts.append(part)
    return message.model_copy(update={"parts": parts})


def redact(text: str) -> str:
    text = _URL.sub(lambda m: public_url(m[0], strip_query=False), text)
    for value in sorted(_KNOWN, key=len, reverse=True):
        text = text.replace(value, MASK)
    text = _PATTERNS[0].sub(MASK, text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{MASK}", text)
    return text


class SecretError(Exception):
    """A secret reference could not be resolved. The message never contains the value."""


def resolve_secret(ref: str) -> str | None:
    """Resolve ``env:NAME`` (or ``none``) to the secret and register it for redaction.

    ``keyring:`` references are reserved for the config work (ARCHITECTURE.md §6.7).
    """
    if ref == "none":
        return None
    if ref.startswith("env:"):
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise SecretError(f"environment variable {name} is not set")
        register_secret(value)
        return value
    if ref.startswith("keyring:"):
        raise SecretError("keyring: references are not supported yet; use env:NAME")
    raise SecretError("secret references must look like env:NAME")
