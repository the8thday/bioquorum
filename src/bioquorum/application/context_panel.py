"""Inspect the model-facing conversation and explicitly compact an idle session."""

import json
from collections import Counter

from bioquorum.agent.budget import BudgetMeter
from bioquorum.agent.compaction import compact_history
from bioquorum.agent.context import conversation, estimated_tokens
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import ModelRole
from bioquorum.models import ModelUnavailableError, Router
from bioquorum.policy.classification import PolicyViolation
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.evidence import result_text, source_aliases
from bioquorum.tools.context import ToolContext


def view(controls: ResearchControls, sid: str, worker: str | None = None) -> dict:
    controls.require_session(sid)
    ws = controls.ws
    options = ws.agent_options()
    events = controls.store.history(sid)
    config = ws.models_config()
    router = Router(config)
    if worker:
        router = router.select_worker(worker, ws.classification)
    routes = router.chain(ModelRole.WORKER, ws.classification)
    model = str(routes[0].spec) if routes else None
    messages = conversation(
        events,
        source_labels=options.binding,
        references=options.allows_references,
        load_event=controls.store.get,
        compacted=options.compact_at_bytes is not None,
    )
    requests = {}
    skills, notes, summaries = set(), set(), []
    for e in events:
        p = e.parsed()
        if isinstance(p, ev.ContextCompacted):
            summaries.append(p.model_dump())
        elif isinstance(p, ev.ToolCallRequested):
            requests[p.call_id] = p.args
        elif isinstance(p, ev.ToolCallFinished) and not p.result.is_error:
            if p.tool == "open_skill":
                name = requests.get(p.call_id, {}).get("name")
                if isinstance(name, str):
                    skills.add(name)
            elif p.tool == "read_project_note":
                try:
                    note = json.loads(result_text(p.result))
                except ValueError:
                    continue
                if isinstance(note, dict) and isinstance(note.get("revision"), str):
                    notes.add(note["revision"])
    return {
        "message_counts": dict(Counter(m.role.value for m in messages)),
        "estimated_tokens": estimated_tokens(messages),
        "model": model,
        "window": config.context_windows.get(model) if model else None,
        "skills": sorted(skills),
        "notes": len(notes),
        "sources": len(source_aliases(events)),
        "summaries": summaries,
    }


async def compact(controls: ResearchControls, sid: str) -> None:
    controls.ensure_idle()
    controls.require_active_session(sid)
    if controls.demo:
        raise ConflictError("The offline example has no recorded summarizer response")
    events = controls.store.history(sid)
    ws = controls.ws
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        permissions=PermissionPolicy(),
        project_id=ws.project_id,
        classification=ws.classification,
    )
    controls.reviewing = True
    try:
        await compact_history(
            controls.make_client(ws, controls.store),
            ctx,
            events,
            BudgetMeter(ws.agent_options().budget),
            target_bytes=0,
            binding=ws.agent_options().binding,
            attempted_through=events[0].seq - 2,
            cause=events[-1].id,
            manual=True,
            references=ws.agent_options().allows_references,
        )
    except (ModelUnavailableError, PolicyViolation) as exc:
        raise ConflictError("The summarizer could not complete this request") from exc
    finally:
        controls.reviewing = False
    if not controls.store.read(sid, from_seq=events[-1].seq + 1, types=["context_compacted"]):
        raise ConflictError("No shorter summary was available for the older conversation")
