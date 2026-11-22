"""Project-wide task statuses derived from local turn events."""

from bioquorum.agent.literature import usage
from bioquorum.application.controls import ResearchControls
from bioquorum.application.recovery import checkpoint

TASK_EVENTS = (
    "turn_requested",
    "turn_started",
    "turn_resumed",
    "turn_finished",
    "permission_requested",
    "permission_decided",
    "plan_proposed",
    "subtask_started",
    "subtask_finished",
    "literature_started",
    "literature_finished",
)


def task_list(controls: ResearchControls) -> list[dict]:
    rows = []
    for session in controls.sessions():
        current: dict | None = None
        waiting = set()
        children = {}
        for event in controls.store.read(session["id"], types=TASK_EVENTS):
            p = event.payload
            if event.type == "turn_requested":
                current = dict(
                    id=event.id,
                    session=session["id"],
                    title=session["title"],
                    question=p["question"],
                    status="queued",
                    children=[],
                )
                rows.append(current)
                waiting.clear()
            elif current is None:
                continue
            elif event.type in ("subtask_started", "literature_started"):
                child = dict(
                    id=p["task_id"],
                    question=p["question"],
                    specialist=p.get("specialist"),
                    status="running",
                )
                current["children"].append(child)
                children[p["task_id"]] = child
            elif event.type in ("subtask_finished", "literature_finished"):
                if child := children.get(p["task_id"]):
                    child.update(
                        status=p["outcome"],
                        **{
                            key: p.get(key, 0)
                            for key in (
                                "input_tokens",
                                "output_tokens",
                                "model_calls",
                                "priced_calls",
                                "cost_usd",
                                "failed_calls",
                            )
                        },
                    )
            elif event.type == "turn_finished" and p["turn_id"] == current["id"]:
                current["status"] = p["outcome"]
                waiting.clear()
            elif event.type in ("turn_started", "turn_resumed") and p["turn_id"] == current["id"]:
                current["status"] = "running"
                waiting.clear()
            elif event.type == "plan_proposed" and current["status"] == "queued":
                current["status"] = "awaiting plan"
            elif current["status"] in ("running", "awaiting permission"):
                if event.type == "permission_requested":
                    waiting.add(p["request_id"])
                elif event.type == "permission_decided":
                    waiting.discard(p["request_id"])
                current["status"] = "awaiting permission" if waiting else "running"
        for child in children.values():
            if child["status"] == "running":
                child.update(
                    usage(
                        controls.store.read(
                            child["id"], types=("model_call_finished", "model_call_failed")
                        )
                    )
                )
    return sorted(rows, key=lambda row: row["id"], reverse=True)


def inbox(controls: ResearchControls) -> list[dict]:
    """Collect pending decisions from their authoritative event streams."""
    from bioquorum.application.first_pass import sections
    from bioquorum.application.program_lineage import review_state
    from bioquorum.application.program_record import decision_versions
    from bioquorum.application.programs import render_claim_rows
    from bioquorum.core import events as ev
    from bioquorum.core.enums import Actor
    from bioquorum.review.binding import render
    from bioquorum.review.evidence import Evidence
    from bioquorum.storage import programs

    items: dict[tuple[str, str], dict] = {}
    reviews: dict[str, set[str]] = {}
    for session in controls.sessions():
        sid = session["id"]
        pid = programs.for_session(controls.store.db, controls.ws.project_id, sid)
        if pid and pid not in reviews:
            outstanding, _ = review_state(controls, pid)
            reviews[pid] = set(outstanding)
        first_pass = False
        events = controls.store.read(
            sid,
            types=(
                "permission_requested",
                "permission_decided",
                "clarification_requested",
                "clarification_answered",
                "user_message",
                "first_pass_started",
                "assistant_message",
                "turn_requested",
                "claim_extracted",
                "plan_proposed",
                "plan_approved",
                "plan_abandoned",
                "turn_started",
                "turn_resumed",
                "turn_finished",
            ),
        )

        def add(key: str, kind: str, text: str, sid=sid, title=session["title"]) -> None:
            items[sid, key] = dict(id=key, session=sid, title=title, kind=kind, text=text)

        if proposed := controls.pending(events):
            add(proposed[0].id, "Plan approval", proposed[0].payload["question"])
        latest = next(
            (
                e
                for e in reversed(events)
                if e.type in ("turn_requested", "turn_started", "turn_resumed", "turn_finished")
            ),
            None,
        )
        if latest and latest.payload.get("outcome") == "interrupted":
            point = checkpoint(controls.store.read(sid))
            if point:
                add(point.request.id, "Interrupted research", point.request.payload["question"])

        for event in events:
            p = event.parsed()
            if isinstance(p, ev.PermissionRequested):
                add(p.request_id, "Permission", p.reason)
            elif isinstance(p, ev.PermissionDecided):
                items.pop((sid, p.request_id), None)
            elif isinstance(p, ev.ClarificationRequested):
                add(event.id, "Question", p.question)
            elif isinstance(p, ev.ClarificationAnswered):
                items.pop((sid, p.request_id), None)
            elif isinstance(p, ev.UserMessage) and event.actor is Actor.USER:
                first_pass = False
            elif isinstance(p, ev.FirstPassStarted):
                first_pass = True
            elif isinstance(p, ev.AssistantMessage) and first_pass and not p.message.tool_calls():
                if "Disagreement" in sections(p.message.text()):
                    parts = sections(
                        render(
                            p.message.text(),
                            Evidence.from_events(controls.store.history(sid, to_seq=event.seq - 1)),
                        )
                    )
                    if text := parts.get("Disagreement"):
                        add(event.id, "Disagreement", text)
            elif isinstance(p, ev.TurnRequested):
                for ref in p.references:
                    item = items.get((sid, ref.id))
                    if item and ref.kind == "answer" and ref.excerpt == item["text"]:
                        items.pop((sid, ref.id))
            elif isinstance(p, ev.ClaimExtracted) and pid and p.claim_id in reviews[pid]:
                (claim,) = render_claim_rows(
                    controls,
                    [{"session_id": sid, "source_event_id": p.source_event_id, "text": p.text}],
                )
                add(p.claim_id, "Review", claim["text"])

    by_program: dict[str, list[ev.Event]] = {}
    for event in controls.store.read(
        None, types=("decision_recorded", "decision_revised", "claim_review_cleared")
    ):
        if event.payload["project_id"] == controls.ws.project_id:
            by_program.setdefault(event.payload["program_id"], []).append(event)
    for program in programs.list_programs(controls.store.db, controls.ws.project_id):
        pid = program["id"]
        history = by_program.get(pid, [])
        if not any(e.type in ("decision_recorded", "decision_revised") for e in history):
            continue
        affected = reviews.get(pid)
        if affected is None:
            affected = set(review_state(controls, pid)[0])
        replaced = {e.payload["claim_id"] for e in history if e.type == "claim_review_cleared"}
        for decision in decision_versions(history, affected | replaced):
            if decision["needs_review"]:
                items[pid, decision["id"]] = dict(
                    id=decision["id"],
                    session="",
                    program=pid,
                    title=program["name"],
                    kind="Decision review",
                    text=decision["text"],
                )
    return sorted(items.values(), key=lambda item: item["id"], reverse=True)
