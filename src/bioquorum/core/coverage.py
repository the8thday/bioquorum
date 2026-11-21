"""Read the current research question's coverage ledger from its event prefix (ADR-030)."""

import json
from collections.abc import Sequence
from typing import Any

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.core.messages import TextPart, ToolResultPart

RETRIEVAL = {PermissionClass.CONNECTOR, PermissionClass.NETWORK, PermissionClass.EXTERNAL_MCP}
QUERY_FIELDS = {
    "query",
    "query_strings",
    "term",
    "terms",
    "symbol",
    "gene",
    "name",
    "rsid",
    "pmid",
    "pmids",
    "doi",
    "identifier",
    "id",
    "url",
    "page",
    "pageSize",
    "limit",
    "filter",
    "filters",
    "offset",
    "path",
    "input_files",
}


def returned_count(part: ToolResultPart) -> int | None:
    """Count an explicit result page. Free text and ambiguous objects have unknown cardinality."""
    if part.is_error or not part.content or not all(isinstance(c, TextPart) for c in part.content):
        return None
    raw = part.text().strip()
    # The public query adapter prefixes the serialized response with its URL and digest.
    if raw.startswith("Source URL: "):
        lines = raw.split("\n", 2)
        if len(lines) == 3 and lines[1].startswith("Response SHA-256: "):
            raw = lines[2]
    try:
        result = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if isinstance(result, dict):
        pages = [
            v
            for k, v in result.items()
            if k in {"results", "records", "articles", "studies", "items"}
        ]
        result = pages[0] if len(pages) == 1 else None
    return len(result) if isinstance(result, list) else None


def coverage_view(events: Sequence[ev.Event]) -> dict[str, Any] | None:
    """Never carry query labels or gaps across a researcher turn, including in forked history."""
    start = next(
        (
            i
            for i in range(len(events) - 1, -1, -1)
            if events[i].type == "user_message" and events[i].actor is Actor.USER
        ),
        None,
    )
    if start is None:
        return None
    turn = events[start:]
    if not any(e.type == "coverage_planned" for e in turn):
        return None
    topics: dict[str, str] = {}
    queries: dict[str, dict] = {}
    closed = False
    gaps: list[str] = []
    decisive = ""
    for event in turn:
        if not event.type.startswith(("coverage_", "tool_call_")):
            continue
        p = event.parsed()
        if isinstance(p, ev.CoveragePlanned):
            topics = p.topics
            for entry in queries.values():
                if entry["topic"] not in topics:
                    entry["topic"] = None
        elif isinstance(p, ev.CoverageUpdated):
            for call_id, topic in p.assignments.items():
                if call_id in queries and topic in topics:
                    queries[call_id]["topic"] = topic
        elif isinstance(p, ev.CoverageClosed):
            closed, gaps, decisive = True, p.gaps, p.decisive
        elif isinstance(p, ev.ToolCallRequested) and (
            p.permission in RETRIEVAL or p.tool in ("read_file", "python_execute", "r_execute")
        ):
            queries[p.call_id] = {
                "id": f"q{len(queries) + 1}",
                "call_id": p.call_id,
                "tool": p.tool,
                "scope": {k: v for k, v in p.args.items() if k in QUERY_FIELDS},
                "time": event.ts.isoformat(),
                "topic": None,
                "count": None,
                "status": "pending",
                "reason": "",
            }
        elif isinstance(p, ev.ToolCallFailed | ev.ToolCallFinished) and p.call_id in queries:
            entry = queries[p.call_id]
            if isinstance(p, ev.ToolCallFailed):
                entry.update(status="failed", reason=p.error[:300])
            elif p.result.is_error:
                entry.update(status="failed", reason=p.result.text()[:300])
            else:
                count = (
                    returned_count(p.result)
                    if entry["tool"] not in ("python_execute", "r_execute", "read_file")
                    else None
                )
                entry.update(count=count, status="empty" if count == 0 else "returned")
    return {
        "topics": topics,
        "queries": list(queries.values()),
        "closed": closed,
        "gaps": gaps,
        "decisive": decisive,
    }
