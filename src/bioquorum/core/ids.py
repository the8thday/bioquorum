"""ULID identifiers and UTC timestamps (ARCHITECTURE.md §5.1, ADR-008)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from ulid import ULID

_lock = threading.Lock()
_last: ULID | None = None


def new_id() -> str:
    """Return a new 26-character ULID string, strictly increasing within this process.

    Standard ULIDs are only ordered to the millisecond. We apply the ULID spec's monotonic
    rule (same millisecond -> increment the random part) so that ordering events by ``id``
    is the same as insertion order. ADR-002 relies on this for deterministic replay.
    """
    global _last
    with _lock:
        candidate = ULID()
        if _last is not None and candidate.timestamp <= _last.timestamp:
            candidate = ULID.from_int(int(_last) + 1)
        _last = candidate
        return str(candidate)


def is_id(value: str) -> bool:
    """True if ``value`` parses as a ULID."""
    try:
        ULID.from_str(value)
    except (ValueError, TypeError):
        return False
    return True


def id_time(value: str) -> datetime:
    """The creation time encoded in a ULID, as an aware UTC datetime."""
    return ULID.from_str(value).datetime.astimezone(UTC)


def utc_now() -> datetime:
    """Current time, aware, UTC, millisecond precision (RFC 3339 friendly)."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)
