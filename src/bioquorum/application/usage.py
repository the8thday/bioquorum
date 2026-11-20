"""Attribute projected model usage to local research turns, including literature children."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from bioquorum.application.workspace import Workspace
from bioquorum.core.enums import Actor
from bioquorum.storage.events import EventStore


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    known_usd: float = 0
    unknown_prices: int = 0
    zero_prices: int = 0

    def add(self, row: dict) -> None:
        self.calls += 1
        self.input_tokens += row["input_tokens"]
        self.output_tokens += row["output_tokens"]
        self.cache_read_tokens += row["cache_read_tokens"]
        self.cache_write_tokens += row["cache_write_tokens"]
        self.latency_ms += row["latency_ms"]
        if row["cost_known"]:
            self.known_usd += row["cost_usd"]
            self.zero_prices += int(row["cost_usd"] == 0)
        else:
            self.unknown_prices += 1


def summarize(rows: list[dict]) -> dict:
    total, roles = Totals(), {}
    for row in rows:
        total.add(row)
        roles.setdefault(row["role"], Totals()).add(row)
    return {"total": asdict(total), "roles": {k: asdict(v) for k, v in roles.items()}}


def report(ws: Workspace, store: EventStore, sid: str) -> dict:
    sessions = [r["id"] for r in store.list_sessions(ws.project_id)]
    if sid not in sessions:
        raise LookupError("Session not found")
    children = {}
    for parent in sessions:
        for e in store.read(parent, types=["literature_started"]):
            children[e.payload["task_id"]] = (parent, e.seq)

    markers = store.read(
        sid,
        types=["turn_requested", "turn_started", "turn_finished", "user_message", "review_started"],
    )
    start = end = review = None
    requested = None
    for event in markers:
        if event.type == "turn_requested":
            requested = event.id
            start, end, review = event, None, None
        elif event.type == "turn_started" and event.payload["turn_id"] == requested:
            start = event
        elif event.type == "turn_finished" and event.payload["turn_id"] == requested:
            end, requested = event, None
        elif event.type == "user_message" and event.actor is Actor.USER and requested is None:
            start, end, review = event, None, None
        elif event.type == "review_started" and start and end is None and review is None:
            review = event

    project_rows, session_rows, turn_rows = [], [], []
    for item in store.model_usage_rows([*sessions, *children]):
        source = item["session_id"]
        owner, seq = children.get(source, (source, item["seq"]))
        row = {**item, "role": "subtask" if source in children else item["role"]}
        project_rows.append(row)
        if owner == sid:
            session_rows.append(row)
            if start and seq >= start.seq and (end is None or seq <= end.seq):
                turn_rows.append(row)

    limits = asdict(ws.agent_options().budget)
    exploration = summarize([r for r in turn_rows if r["role"] != "reviewer"])["total"]
    elapsed = None
    if start and start.type != "turn_requested":
        stop = review or end
        if stop or requested:
            elapsed = ((stop.ts if stop else datetime.now(UTC)) - start.ts).total_seconds()
    used = {
        "tokens": exploration["input_tokens"] + exploration["output_tokens"],
        "usd": None if exploration["unknown_prices"] else exploration["known_usd"],
        "seconds": max(0, elapsed) if elapsed is not None else None,
    }
    remaining = {
        k: max(0, limit - used[k]) if limit is not None and used[k] is not None else None
        for k, limit in limits.items()
    }
    return {
        "turn": summarize(turn_rows),
        "session": summarize(session_rows),
        "project": summarize(project_rows),
        "budget": {"limits": limits, "remaining": remaining},
    }
