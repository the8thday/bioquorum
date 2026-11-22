"""Render the research record from events without persisting another copy of state."""

from __future__ import annotations

from typing import Any

from bioquorum.application.artifacts import catalog_records
from bioquorum.application.controls import ResearchControls
from bioquorum.application.first_pass import HEADINGS, sections
from bioquorum.application.onboarding import disconnected
from bioquorum.application.program_lineage import review_state
from bioquorum.core import events as ev
from bioquorum.core.activity import ACTIVITY
from bioquorum.core.enums import Actor
from bioquorum.core.refs import QuoteRef, RunRef, ValueRef
from bioquorum.core.review_record import AnswerReview, answer_reviews
from bioquorum.provenance.recompute import replacement_claims
from bioquorum.review.binding import find_bindings, ground_claim, render
from bioquorum.review.checks.absence import unsupported_absence
from bioquorum.review.evidence import Evidence, result_text
from bioquorum.storage.programs import for_session
from bioquorum.tools.replay import dependency_chain, recorded_inputs

AUDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "model_call_started": ("provider", "model", "role", "classification"),
    "model_call_finished": (
        "provider",
        "model",
        "role",
        "classification",
        "input_tokens",
        "output_tokens",
    ),
    "model_call_failed": ("provider", "model", "role"),
    "tool_call_requested": ("tool", "permission"),
    "tool_call_finished": ("tool", "duration_ms"),
    "tool_call_failed": ("tool",),
    "permission_requested": ("permission",),
    "permission_decided": ("permission", "granted", "scope"),
    "run_started": ("run_id", "isolation", "language"),
    "run_finished": ("run_id",),
    "run_failed": ("run_id",),
    "run_cancelled": ("run_id",),
    "run_timed_out": ("run_id",),
    "literature_started": ("task_id",),
    "literature_finished": ("task_id", "outcome"),
    "bundle_exported": ("bundle_id",),
    "error": ("where", "policy_violation"),
}


def _fields(event: ev.Event, fields: tuple[str, ...]) -> dict:
    data = {key: event.payload.get(key) for key in fields}
    if event.type == "tool_call_finished":
        payload = event.parsed()
        assert isinstance(payload, ev.ToolCallFinished)
        data["is_error"] = payload.result.is_error
        if payload.result.is_error and result_text(payload.result).startswith(
            "replay: no recorded response"
        ):
            data["replay_miss"] = True
        if payload.result.empty_result():
            data["empty_result"] = True
    return data


def audit_log(controls: ResearchControls, sid: str, *, before: int | None, limit: int) -> dict:
    """Read session-local policy and execution metadata without prompts or tool arguments."""
    history = controls.require_session(sid)
    if not 1 <= limit <= 100 or (before is not None and not 1 <= before <= history[-1].seq + 1):
        raise ValueError("Invalid audit cursor or page size")
    records = []
    for event in reversed(history):
        if event.type not in AUDIT_FIELDS or (before is not None and event.seq >= before):
            continue
        records.append(
            {
                "seq": event.seq,
                "type": event.type,
                "actor": event.actor.value,
                "time": event.ts.isoformat(),
                "details": _fields(event, AUDIT_FIELDS[event.type]),
            }
        )
        if len(records) > limit:
            break
    return {
        "classification": controls.ws.classification.value,
        "entries": records[:limit],
        "next_before": records[limit - 1]["seq"] if len(records) > limit else None,
    }


def _bindings(text: str, evidence: Evidence) -> list[dict]:
    pieces: list[dict] = []
    end = 0
    for b in find_bindings(text, evidence):
        if b.start > end:
            pieces.append({"text": text[end : b.start]})
        value = b.ref
        if isinstance(value, ValueRef | QuoteRef):
            pieces.append({"text": b.rendered, "raw": b.raw, "ref": value.model_dump(mode="json")})
        else:
            pieces.append({"text": b.raw, "problem": b.problem})
        end = b.end
    if end < len(text):
        pieces.append({"text": text[end:]})
    return pieces


def _answer(
    event: ev.Event,
    text: str,
    prefix: list[ev.Event],
    review: AnswerReview,
    statuses: dict[str, str],
    findings: dict[str, list[dict]],
    pending_claims: dict[str, set[str]],
    replacements: dict[str, tuple[str, str]],
) -> dict:
    evidence = Evidence.from_events(prefix)
    start = next(
        (
            i
            for i in range(len(prefix) - 1, -1, -1)
            if prefix[i].type == "user_message" and prefix[i].actor is Actor.USER
        ),
        len(prefix),
    )
    first_pass = any(e.type == "first_pass_started" for e in prefix[start:])
    clarification = next(
        ({"id": e.id, **e.payload} for e in prefix[start:] if e.type == "clarification_requested"),
        None,
    )
    parts = sections(render(text, evidence)) if first_pass else {}
    lines = text.splitlines()
    return {
        "id": event.id,
        "coverage": evidence.coverage,
        "first_pass": first_pass,
        "discussion_items": {k: v for k, v in parts.items() if k in ("Disagreement", "Next step")},
        "missing_sections": [h for h in HEADINGS if h not in parts] if first_pass else [],
        "clarification": clarification,
        "seq": event.seq,
        "inherited": event.seq < 0,
        "review_completed": review.review_id is not None,
        "review_limitations": review.limitations,
        "claims": [
            {
                "segments": _bindings(c.text, evidence),
                "id": c.claim_id,
                "has_calculation": (
                    any(isinstance(ref, (RunRef, ValueRef)) for ref in c.refs)
                    and not any(isinstance(ref, QuoteRef) for ref in c.refs)
                ),
                "status": statuses.get(c.claim_id, "unverified"),
                "findings": findings.get(c.claim_id, []),
                "needs_review": c.claim_id in pending_claims,
                "replacement_claim_id": replacements[c.claim_id][0]
                if c.claim_id in replacements
                else None,
                "replacement_session_id": replacements[c.claim_id][1]
                if c.claim_id in replacements
                else None,
            }
            for c in review.claims
        ],
        "paragraphs": [
            {
                "segments": _bindings(line, evidence),
                "unscoped_absence": unsupported_absence(line, evidence.coverage),
                "unbound": list(ground_claim(line, evidence).unbound),
            }
            for line in lines
            if line.strip()
        ],
    }


def snapshot(controls: ResearchControls, sid: str) -> dict:
    local = controls.require_session(sid)
    program_id = for_session(controls.store.db, controls.ws.project_id, sid)
    pending_claims: dict[str, set[str]] = {}
    replacements: dict[str, tuple[str, str]] = {}
    if program_id:
        review_events = controls.store.read(
            None,
            types=(
                "claim_review_flagged",
                "claim_review_cleared",
                "recompute_started",
                "recompute_finished",
            ),
        )
        pending_claims, _ = review_state(controls, program_id)
        replacements = replacement_claims(review_events, controls.ws.project_id, program_id)
    events = controls.store.history(sid)
    parsed = [(e, e.parsed()) for e in events]
    rerun_ids = {
        payload.run_id
        for _, payload in parsed
        if isinstance(payload, ev.RunStarted) and payload.verification_run
    }
    statuses: dict[str, str] = {}
    findings: dict[str, list[dict]] = {}
    models = {
        p.call_id: f"{p.provider}:{p.model}"
        for _, p in parsed
        if isinstance(p, ev.ModelCallFinished)
    }
    reviews = answer_reviews(events)
    permission_requests: dict[str, dict] = {}
    turns: dict[str, dict] = {}
    plans: dict[str, dict] = {}
    pending = controls.pending(local)
    for e, p in parsed:
        if isinstance(p, ev.ClaimStatusChanged):
            statuses[p.claim_id] = p.to_status.value
        elif isinstance(p, ev.ReviewFinding) and p.claim_id:
            findings.setdefault(p.claim_id, []).append(
                {"check": p.check, "severity": p.severity.value, "evidence": p.evidence}
            )
        elif isinstance(p, ev.TurnRequested):
            turns[e.id] = {
                "id": e.id,
                "seq": e.seq,
                "question": p.question,
                "selections": [
                    r.model_dump(exclude_none=True) for r in p.references if r.kind == "artifact"
                ],
                "status": "queued",
                "plan_id": p.plan_id,
            }
        elif isinstance(p, ev.TurnStarted) and p.turn_id in turns:
            turns[p.turn_id]["status"] = "running"
        elif isinstance(p, ev.TurnFinished) and p.turn_id in turns:
            turns[p.turn_id].update(status=p.outcome, error=p.error)
        elif isinstance(p, ev.PlanProposed):
            plans[p.plan_id] = {
                "steps": p.steps,
                "status": "proposed",
                "done": [],
                "deviations": [],
            }
        elif isinstance(p, ev.PlanApproved) and p.plan_id in plans:
            plans[p.plan_id]["status"] = "approved"
        elif isinstance(p, ev.PlanAbandoned) and p.plan_id in plans:
            plans[p.plan_id].update(status="declined", reason=p.reason)
        elif isinstance(p, ev.PlanStepDone) and p.plan_id in plans:
            plans[p.plan_id]["done"].append(p.step_index)
        elif isinstance(p, ev.PlanDeviation) and p.plan_id in plans:
            plans[p.plan_id]["deviations"].append({"change": p.change, "reason": p.reason})
        elif isinstance(p, ev.PermissionRequested) and e.seq >= 0:
            permission_requests[p.request_id] = p.model_dump(mode="json")
        elif isinstance(p, ev.PermissionDecided):
            permission_requests.pop(p.request_id, None)
    live = controls.tasks.get(sid)
    questions = []
    current_turn = None
    answer_turns = {}
    for e, p in parsed:
        if isinstance(p, ev.TurnStarted):
            current_turn = p.turn_id
        elif isinstance(p, ev.TurnFinished | ev.SessionForked):
            current_turn = None
        elif isinstance(p, ev.AssistantMessage):
            answer_turns[e.id] = current_turn
        elif isinstance(p, ev.UserMessage) and e.actor is Actor.USER and current_turn is None:
            questions.append({"id": e.id, "seq": e.seq, "text": p.message.text()})
    return {
        "last_seq": local[-1].seq,
        "questions": questions,
        "turns": [{**turn, "plan": plans.get(turn["plan_id"])} for turn in turns.values()],
        "pending_plan": pending[0].id if pending else None,
        "verification": [
            {
                "type": e.type,
                "result": p.details
                if isinstance(p, ev.RerunFinished)
                else p.model_dump(mode="json"),
            }
            for e, p in parsed
            if isinstance(p, ev.RerunFinished | ev.ReplicationFinished)
        ],
        "bundles": [p.bundle_id for _, p in parsed if isinstance(p, ev.BundleExported)],
        "permissions": [
            p for key, p in permission_requests.items() if live and key in live.permissions
        ],
        "answers": [
            {
                **_answer(
                    e,
                    p.message.text(),
                    events[:index],
                    reviews.get(e.id, AnswerReview()),
                    statuses,
                    findings,
                    pending_claims,
                    replacements,
                ),
                "turn": answer_turns.get(e.id),
                "model": models.get(p.model_call_id) if p.model_call_id else None,
            }
            for index, (e, p) in enumerate(parsed)
            if isinstance(p, ev.AssistantMessage)
            and not p.message.tool_calls()
            and p.message.text().strip()
        ],
        "disconnected": disconnected(controls),
        "activity": [
            {
                "seq": e.seq,
                "id": e.id,
                "type": e.type,
                "time": e.ts.isoformat(),
                **_fields(e, ACTIVITY[e.type]),
            }
            for e in events
            if e.type in ACTIVITY
        ][-100:],
        "products": catalog_records(parsed),
        "figures": [
            {"id": e.id, "hash": p.ref.content_hash, "run_id": p.run_id, "seq": e.seq}
            for e, p in parsed
            if isinstance(p, ev.RunOutput) and p.ref is not None and p.run_id not in rerun_ids
        ],
    }


def binding(
    controls: ResearchControls, sid: str, answer_id: str, reference: str, kind: str
) -> dict:
    controls.require_session(sid)
    history = controls.store.history(sid)
    for index, e in enumerate(history):
        if e.id == answer_id and isinstance(answer := e.parsed(), ev.AssistantMessage):
            prior = history[:index]
            break
    else:
        raise LookupError("Answer not found")
    evidence = Evidence.from_events(prior)
    refs = [b.ref for b in find_bindings(answer.message.text(), evidence) if b.ok]
    if kind == "quote":
        if not any(isinstance(ref, QuoteRef) and ref.call_id == reference for ref in refs):
            raise LookupError("Source is not bound to this answer")
        source = next(s for s in evidence.sources if s.call_id == reference)
        data = {"source": source.text, "tool": source.tool, "alias": source.alias}
        if source.source_event_id and source.task_id:
            original = controls.store.get(source.source_event_id)
            if (
                original is not None
                and original.session_id == source.task_id
                and isinstance(p := original.parsed(), ev.ToolCallFinished)
                and p.call_id == source.call_id
                and p.tool == source.tool
                and not p.result.is_error
            ):
                full = result_text(p.result)
                if len(full) <= 100_000:
                    data["source"] = full
                else:
                    data["scope"] = "Validated excerpt; retrieved result exceeds the preview limit"
            data["task_id"] = source.task_id
        return data
    if not any(isinstance(ref, ValueRef) and ref.run_id == reference for ref in refs):
        raise LookupError("Run is not bound to this answer")
    return run_details(prior, reference)


def run_details(events: list[ev.Event], run_id: str) -> dict:
    details: dict[str, Any] = {"outputs": [], "figures": [], "values": [], "lineage": []}
    start = None
    for e in events:
        p = e.parsed()
        if isinstance(p, ev.RunStarted) and p.run_id == run_id:
            start = p
            details.update(
                code=p.code,
                code_hash=p.code_hash,
                language=p.language,
                isolation=p.isolation,
                environment=p.environment,
                env_lock=p.env_lock_hash,
                synthetic=p.synthetic,
                inputs=[r.model_dump(mode="json") for r in p.inputs],
            )
        elif isinstance(p, ev.RunOutput) and p.run_id == run_id:
            if p.text is not None:
                details["outputs"].append({"text": p.text, "stream": p.stream.value})
            if p.ref:
                details["figures"].append(p.ref.content_hash)
        elif isinstance(p, ev.LineageObserved) and p.run_id == run_id:
            details["lineage"].append(p.model_dump(mode="json"))
        elif isinstance(p, ev.RunFinished) and p.run_id == run_id:
            details["values"] = [v.model_dump(mode="json") for v in p.values]
            if p.stdout_ref:
                details["stdout_hash"] = p.stdout_ref.content_hash
    if start is None:
        raise LookupError("Run not found")
    try:
        chain = dependency_chain(events, start)
        details["previous_cells"] = [
            {"code": p.code, "run_id": p.run_id, "language": p.language} for p in chain[:-1]
        ]
        details["inputs"] = [
            {**ref.model_dump(mode="json"), "path": path}
            for path, ref in recorded_inputs(chain).items()
        ]
    except ValueError:
        # Older records still display their own inputs even if automatic replay is unavailable.
        pass
    return details
