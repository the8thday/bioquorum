"""Project-scoped program controls and their current research record."""

from bioquorum.application.controls import ResearchControls
from bioquorum.application.project_tools import invoke_project
from bioquorum.core import events as ev
from bioquorum.core.review_record import answer_reviews
from bioquorum.core.status import VerificationStatus
from bioquorum.review.binding import render
from bioquorum.review.evidence import Evidence
from bioquorum.storage import programs as projected
from bioquorum.tools.programs import ATTACH, CLOSE, CREATE, NEW_SESSION, RENAME


def list_programs(controls: ResearchControls) -> list[dict]:
    return projected.list_programs(controls.store.db, controls.ws.project_id)


def render_claim_rows(
    controls: ResearchControls, rows: list[dict], members: frozenset[str] | None = None
) -> list[dict]:
    """Resolve any current or historical claim against its answer-time evidence."""
    by_session: dict[str, dict[str, list[dict]]] = {}
    rendered = [dict(row) for row in rows]
    for row in rendered:
        by_session.setdefault(row["session_id"], {}).setdefault(row["source_event_id"], []).append(
            row
        )
    for sid, targets in by_session.items():
        history = controls.store.history(sid, members=members)
        for i, event in enumerate(history):
            if event.id in targets:
                evidence = Evidence.from_events(history[:i])
                for row in targets[event.id]:
                    row["text"] = render(row["text"], evidence)
    return rendered


def _current_claims(
    controls: ResearchControls, session_id: str, members: frozenset[str] | None = None
) -> list[dict]:
    """Keep each attached session's own current claims; use ancestry only to resolve bindings."""
    history = controls.store.history(session_id, members=members)
    reviews = answer_reviews(history)
    position = {event.id: index for index, event in enumerate(history)}
    local_claims = {
        event.payload["claim_id"]
        for event in history
        if event.seq >= 0 and event.type == ev.ClaimExtracted.event_type
    }
    statuses = {
        event.payload["claim_id"]: event.payload["to_status"]
        for event in history
        if event.type == ev.ClaimStatusChanged.event_type
    }
    rows = []
    for target, review in reviews.items():
        current = [claim for claim in review.claims if claim.claim_id in local_claims]
        if not current:
            continue
        evidence = Evidence.from_events(history[: position.get(target, 0)])
        for claim in current:
            rows.append(
                {
                    "id": claim.claim_id,
                    "session_id": session_id,
                    "source_event_id": target,
                    "text": render(claim.text, evidence),
                    "kind": claim.kind.value,
                    "refs": [ref.model_dump(mode="json") for ref in claim.refs],
                    "status": statuses.get(claim.claim_id, VerificationStatus.UNVERIFIED.value),
                }
            )
    return rows


def show(controls: ResearchControls, program_id: str) -> dict:
    program = projected.show(controls.store.db, controls.ws.project_id, program_id)
    members = frozenset(s["id"] for s in program["sessions"])
    return {
        **program,
        "claims": [
            claim
            for session in program["sessions"]
            for claim in _current_claims(controls, session["id"], members)
        ],
    }


async def create(controls: ResearchControls, name: str) -> dict:
    program_id = await invoke_project(controls, CREATE, {"name": name})
    return show(controls, program_id)


async def rename(controls: ResearchControls, program_id: str, name: str) -> dict:
    await invoke_project(controls, RENAME, {"program_id": program_id, "name": name})
    return show(controls, program_id)


async def close(controls: ResearchControls, program_id: str) -> dict:
    await invoke_project(controls, CLOSE, {"program_id": program_id})
    return show(controls, program_id)


async def attach(controls: ResearchControls, program_id: str, session_id: str) -> dict:
    await invoke_project(controls, ATTACH, {"program_id": program_id, "session_id": session_id})
    return show(controls, program_id)


async def new_session(controls: ResearchControls, program_id: str, title: str) -> str:
    return await invoke_project(controls, NEW_SESSION, {"program_id": program_id, "name": title})
