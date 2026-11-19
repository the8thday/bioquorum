"""Render the research record from events without persisting another copy of state."""

from __future__ import annotations

from typing import Any

from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.core.refs import QuoteRef, ValueRef
from bioquorum.review.binding import find_bindings, ground_claim
from bioquorum.review.evidence import Evidence, result_text
from bioquorum.tools.replay import recorded_inputs, run_chain

ACTIVITY: dict[str, tuple[str, ...]] = {
    "model_call_started": ("provider", "model", "role"),
    "model_call_finished": (
        "model",
        "role",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_known",
    ),
    "model_call_failed": ("model", "role", "error"),
    "tool_call_requested": ("tool",),
    "tool_call_finished": ("tool", "duration_ms"),
    "tool_call_failed": ("tool", "error"),
    "run_finished": ("run_id",),
    "run_failed": ("run_id", "error"),
    "run_cancelled": ("run_id",),
    "run_timed_out": ("run_id", "timeout_s"),
    "permission_decided": ("permission", "target", "granted"),
    "plan_step_done": ("plan_id", "step_index"),
    "plan_deviation": ("plan_id", "change", "reason"),
    "review_started": ("review_id", "target_event_id"),
    "review_finished": ("review_id", "finding_count"),
    "exploration_stopped": ("reason",),
    "error": ("where", "error"),
    "rerun_started": ("claim_id",),
    "rerun_finished": ("matched",),
    "replication_started": ("claim_id",),
    "replication_finished": ("claim_id", "outcome"),
    "bundle_exported": ("bundle_id",),
    "literature_started": ("task_id", "question"),
    "literature_finished": (
        "task_id",
        "outcome",
        "reason",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_known",
    ),
}

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
                "details": {key: event.payload.get(key) for key in AUDIT_FIELDS[event.type]},
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
    claims: list[ev.ClaimExtracted],
    statuses: dict[str, str],
    findings: dict[str, list[dict]],
) -> dict:
    evidence = Evidence.from_events(prefix)
    lines = text.splitlines()
    return {
        "id": event.id,
        "seq": event.seq,
        "inherited": event.seq < 0,
        "claims": [
            {
                "segments": _bindings(c.text, evidence),
                "id": c.claim_id,
                "has_calculation": (
                    any(isinstance(ref, ValueRef) for ref in c.refs)
                    and not any(isinstance(ref, QuoteRef) for ref in c.refs)
                ),
                "status": statuses.get(c.claim_id, "unverified"),
                "findings": findings.get(c.claim_id, []),
            }
            for c in claims
        ],
        "paragraphs": [
            {
                "segments": _bindings(line, evidence),
                "unbound": list(ground_claim(line, evidence).unbound),
            }
            for line in lines
            if line.strip()
        ],
    }


def snapshot(controls: ResearchControls, sid: str) -> dict:
    local = controls.require_session(sid)
    events = controls.store.history(sid)
    parsed = [(e, e.parsed()) for e in events]
    rerun_ids = {
        payload.run_id
        for _, payload in parsed
        if isinstance(payload, ev.RunStarted) and payload.verification_run
    }
    statuses: dict[str, str] = {}
    findings: dict[str, list[dict]] = {}
    claims: dict[str, list[ev.ClaimExtracted]] = {}
    review_targets: dict[str, str] = {}
    review_claims: dict[str, list[ev.ClaimExtracted]] = {}
    permission_requests: dict[str, dict] = {}
    turns: dict[str, dict] = {}
    plans: dict[str, dict] = {}
    pending = controls.pending(local)
    for e, p in parsed:
        if isinstance(p, ev.ReviewStarted):
            review_targets[p.review_id] = p.target_event_id
            review_claims[e.id] = []
        elif isinstance(p, ev.ClaimExtracted) and e.causation_id in review_claims:
            review_claims[e.causation_id].append(p)
        elif isinstance(p, ev.ReviewFinished) and e.causation_id in review_claims:
            target = review_targets.get(p.review_id)
            if target:
                claims[target] = review_claims[e.causation_id]
        elif isinstance(p, ev.ClaimStatusChanged):
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
    for e, p in parsed:
        if isinstance(p, ev.TurnStarted):
            current_turn = p.turn_id
        elif isinstance(p, ev.TurnFinished | ev.SessionForked):
            current_turn = None
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
            _answer(e, p.message.text(), events[:index], claims.get(e.id, []), statuses, findings)
            for index, (e, p) in enumerate(parsed)
            if isinstance(p, ev.AssistantMessage)
            and not p.message.tool_calls()
            and p.message.text().strip()
        ],
        "activity": [
            {
                "seq": e.seq,
                "type": e.type,
                "time": e.ts.isoformat(),
                **{key: e.payload.get(key) for key in ACTIVITY[e.type]},
            }
            for e in events
            if e.type in ACTIVITY
        ][-100:],
        "figures": [
            {"hash": p.ref.content_hash, "run_id": p.run_id, "seq": e.seq}
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
        chain = run_chain(events, start)
        details["previous_cells"] = [{"code": p.code, "run_id": p.run_id} for p in chain[:-1]]
        details["inputs"] = [
            {**ref.model_dump(mode="json"), "path": path}
            for path, ref in recorded_inputs(chain).items()
        ]
    except ValueError:
        # Older records still display their own inputs even if automatic replay is unavailable.
        pass
    return details
