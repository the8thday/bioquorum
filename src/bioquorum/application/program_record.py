"""Researcher decisions and portable handoffs, all navigable to their source events."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bioquorum.application import materials, programs, verify
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.application.program_lineage import review_state
from bioquorum.application.project_tools import invoke_project
from bioquorum.core import events as ev
from bioquorum.core.coverage import coverage_view
from bioquorum.core.enums import Actor, PermissionClass
from bioquorum.core.hashing import canonical_json, sha256_bytes, sha256_file, sha256_stream
from bioquorum.core.ids import new_id
from bioquorum.policy.secrets import recorded_args, redact
from bioquorum.storage import programs as projected
from bioquorum.tools import ToolContext, ToolResult, ToolSpec


class DecisionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    claim_ids: list[str] = Field(min_length=1, max_length=100)
    prior_revision: str | None = None
    claim_revisions: dict[str, str] | None = None

    @field_validator("text")
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if not value or redact(value) != value:
            raise ValueError("Enter a decision without credentials")
        return value

    @field_validator("claim_ids")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Cite each claim once")
        return value


class Export(BaseModel):
    program_id: str


def _log(controls: ResearchControls, pid: str) -> list[ev.Event]:
    return projected.event_log(controls.store.db, controls.ws.project_id, pid)


def _decisions(events: list[ev.Event], affected: set[str]) -> list[dict]:
    timelines: dict[str, list[dict]] = {}
    for event in events:
        if event.type not in ("decision_recorded", "decision_revised"):
            continue
        p = event.parsed()
        assert isinstance(p, ev.DecisionRecorded)
        refs = [c.model_dump(mode="json") for c in p.claims]
        timelines.setdefault(p.decision_id, []).append(
            {
                "id": p.decision_id,
                "revision": event.id,
                "event_id": event.id,
                "text": p.text,
                "claims": refs,
                "time": event.ts.isoformat(),
                "needs_review": any(c.id in affected for c in p.claims),
            }
        )
    return [{**timeline[-1], "history": timeline} for timeline in timelines.values()]


def _gaps(events: list[ev.Event]) -> list[dict]:
    turns: dict[str, list[ev.Event]] = {}
    rows = []

    def finish(turn):
        if not turn or not (view := coverage_view(turn)):
            return
        ledger_id = next(e.id for e in reversed(turn) if e.type.startswith("coverage_"))
        gaps = list(view["gaps"])
        if not view["closed"]:
            seen = {q["topic"] for q in view["queries"] if q["status"] == "returned"}
            gaps.extend(title for topic, title in view["topics"].items() if topic not in seen)
        for text in dict.fromkeys(gaps):
            rows.append({"text": text, "session_id": turn[0].session_id, "event_id": ledger_id})

    for event in events:
        if event.session_id is None:
            continue
        turn = turns.setdefault(event.session_id, [])
        if event.type == "user_message" and event.actor is Actor.USER:
            finish(turn)
            turn.clear()
        turn.append(event)
    for turn in turns.values():
        finish(turn)
    return rows


def handoff(controls: ResearchControls, pid: str) -> dict:
    return _snapshot(controls, pid)[0]


def _snapshot(controls: ResearchControls, pid: str) -> tuple[dict, list[ev.Event]]:
    for _ in range(2):
        events = _log(controls, pid)
        summary = _handoff(controls, pid, events)
        if (
            projected.head(controls.store.db, controls.ws.project_id, pid)
            == summary["snapshot_event_id"]
        ):
            return summary, events
    raise ConflictError("Program changed while generating the handoff; refresh to read it")


def _handoff(controls: ResearchControls, pid: str, events: list[ev.Event]) -> dict:
    program = programs.show(controls, pid)
    outstanding, flagged = review_state(controls, pid)
    replacements = {
        e.payload["claim_id"]: e.payload["replacement_claim_id"]
        for e in events
        if e.type == "claim_review_cleared"
    }
    claim_events = {}
    revisions = {}
    session_events = {}
    title_id = None
    for event in events:
        p = event.payload
        if event.type == "program_created" or (event.type == "program_updated" and p.get("name")):
            title_id = event.id
        elif event.type in ("session_created", "session_forked", "session_title_set"):
            session_events[event.session_id] = event.id
        elif event.type == "claim_extracted":
            claim_events[p["claim_id"]] = event
            revisions[p["claim_id"]] = event.id
        elif event.type == "claim_status_changed":
            revisions[p["claim_id"]] = event.id
    claims = [
        {
            **c,
            "event_id": claim_events[c["id"]].id,
            "revision": revisions[c["id"]],
            "needs_review": c["id"] in outstanding,
        }
        for c in program["claims"]
        if c["id"] in claim_events and c["id"] not in replacements
    ]
    pending = [
        {
            "id": cid,
            "session_id": claim_events[cid].session_id,
            "text": claim_events[cid].payload["text"],
            "source_event_id": claim_events[cid].payload["source_event_id"],
            "material_version_ids": sorted(versions),
            "event_id": flagged[(cid, sorted(versions)[0])],
        }
        for cid, versions in outstanding.items()
        if cid in claim_events
    ]
    return {
        **program,
        "event_id": title_id,
        "snapshot_event_id": events[-1].id if events else None,
        "question": program["name"],
        "sessions": [
            {**s, "event_id": session_events[s["id"]]}
            for s in program["sessions"]
            if s["id"] in session_events
        ],
        "claims": claims,
        "pending": programs.render_claim_rows(
            controls, pending, frozenset(s["id"] for s in program["sessions"])
        ),
        "gaps": _gaps(events),
        "materials": [{**m, "event_id": m["id"]} for m in materials.catalog(controls, pid)],
        "decisions": _decisions(events, set(outstanding) | set(replacements)),
    }


async def save_decision(
    controls: ResearchControls,
    pid: str,
    text: str,
    claim_ids: list[str],
    decision_id: str | None = None,
    prior_revision: str | None = None,
    claim_revisions: dict[str, str] | None = None,
) -> dict:
    async def save(args: DecisionEdit, ctx: ToolContext[None]) -> ToolResult:
        current = handoff(controls, pid)
        selected = {c["id"]: c for c in current["claims"]}
        if not all(cid in selected for cid in args.claim_ids):
            raise ValueError("Select a current claim in this research line")
        versions = {cid: selected[cid]["revision"] for cid in args.claim_ids}
        if args.claim_revisions is not None and args.claim_revisions != versions:
            raise ValueError("Claim revision changed; refresh the evidence before saving")
        refs = [
            ev.DecisionClaim(**{k: selected[cid][k] for k in ev.DecisionClaim.model_fields})
            for cid in args.claim_ids
        ]
        fields = dict(
            project_id=controls.ws.project_id,
            program_id=pid,
            decision_id=decision_id or new_id(),
            text=args.text,
            claims=refs,
        )
        if decision_id is None and args.prior_revision is None:
            payload = ev.DecisionRecorded.model_validate(fields)
        elif decision_id and args.prior_revision:
            payload = ev.DecisionRevised.model_validate(
                {**fields, "prior_revision": args.prior_revision}
            )
        else:
            raise ValueError("A decision revision must name its prior revision")
        ctx.store.append(
            payload, session_id=None, actor=Actor.USER, causation_id=ctx.requested_event_id
        )
        return ToolResult.text(payload.decision_id)

    action = ToolSpec[None](
        "save_decision",
        "Save a researcher's decision against fixed evidence versions.",
        save,
        PermissionClass.WRITE_FS,
        args_model=DecisionEdit,
    )
    try:
        saved = await invoke_project(
            controls,
            action,
            {
                "text": text,
                "claim_ids": claim_ids,
                "prior_revision": prior_revision,
                "claim_revisions": claim_revisions,
            },
        )
    except ValueError as error:
        if "revision changed" in str(error):
            raise ConflictError(
                "A decision or claim revision changed; refresh the evidence before saving"
            ) from error
        raise
    return next(d for d in handoff(controls, pid)["decisions"] if d["id"] == saved)


_FIELDS = {
    "program_created": ("name",),
    "program_updated": ("name", "closed"),
    "session_attached": ("session_id",),
    "session_created": ("title",),
    "session_forked": ("title", "parent_session_id"),
    "session_title_set": ("title",),
    "coverage_closed": ("gaps", "decisive"),
    "coverage_planned": ("topics",),
    "coverage_updated": ("assignments",),
    "claim_extracted": ("claim_id", "text", "kind", "source_event_id", "refs"),
    "claim_status_changed": ("claim_id", "from_status", "to_status", "trigger"),
    "decision_recorded": ("text", "claims", "decision_id"),
    "decision_revised": ("text", "claims", "decision_id", "prior_revision"),
    "material_registered": ("source", "kind", "upload_event_id"),
    "material_superseded": ("source", "reason", "prior_version_id"),
    "material_withdrawn": ("reason", "version_id"),
    "claim_review_flagged": ("claim_id", "material_version_id"),
    "claim_review_cleared": ("claim_id", "replacement_claim_id", "material_version_id"),
    "run_started": ("run_id", "language", "code_hash"),
    "run_finished": ("run_id", "values"),
    "artifact_version_created": ("name", "run_id", "content_hash"),
    "bundle_exported": ("bundle_id", "manifest_hash"),
    "program_bundle_exported": ("bundle_id", "manifest_hash"),
}


def record(controls: ResearchControls, pid: str, event_id: str) -> dict:
    return _record(projected.record(controls.store.db, controls.ws.project_id, pid, event_id))


def _record(event: ev.Event) -> dict:
    if event.type in ("user_message", "assistant_message"):
        p = event.parsed()
        assert isinstance(p, ev.UserMessage | ev.AssistantMessage)
        if p.message.tool_calls():
            raise LookupError("No public research record for this event")
        details = {"text": redact(p.message.text())}
    elif event.type in _FIELDS:
        details = {k: event.payload[k] for k in _FIELDS[event.type] if k in event.payload}
    else:
        raise LookupError("No public research record for this event")
    return {
        "id": event.id,
        "session_id": event.session_id,
        "type": event.type,
        "time": event.ts.isoformat(),
        "details": recorded_args("program_record", details),
    }


async def export(controls: ResearchControls, pid: str) -> Path:
    """Nest standard portable session bundles and include each version's verified source bytes."""

    async def pack(args: Export, ctx: ToolContext[None]) -> ToolResult:
        controls.ensure_idle()
        summary, events = _snapshot(controls, args.program_id)
        through = {e.session_id: e.seq for e in events if e.session_id is not None}
        bundle_id = new_id()
        dest = controls.ws.state_dir / "exports"
        dest.mkdir(exist_ok=True)
        members = frozenset(session["id"] for session in summary["sessions"])
        session_exports = []
        for session in summary["sessions"]:
            sid = session["id"]
            child = await verify.export_bundle(
                controls, sid, program_members=members, through_seq=through[sid]
            )
            session_exports.append(
                (sid, child["id"], verify.bundle_file(controls, sid, child["id"]))
            )
        with TemporaryDirectory(dir=dest) as tmp:
            zipped = Path(tmp) / "record.zip"
            digests = {}
            with ZipFile(zipped, "w", compression=ZIP_STORED) as archive:
                data = canonical_json(summary)
                archive.writestr("handoff.json", data)
                digests["handoff.json"] = sha256_bytes(data)
                records = []
                for event in events:
                    try:
                        records.append(_record(event))
                    except LookupError:
                        continue
                data = canonical_json(records)
                archive.writestr("records.json", data)
                digests["records.json"] = sha256_bytes(data)
                for material in summary["materials"]:
                    path, _ = materials.download(controls, pid, material["id"])
                    name = "objects/" + material["hash"]
                    if name not in digests:
                        archive.write(path, name)
                        digests[name] = material["hash"]
                for sid, _, path in session_exports:
                    name = "sessions/" + sid + ".zip"
                    digests[name] = sha256_file(path)
                    archive.write(path, name)
                manifest = canonical_json(
                    {"schema": "bioquorum-program/1", "program_id": pid, "files": digests}
                )
                archive.writestr("manifest.json", manifest)
            with ZipFile(zipped) as archive:
                for name, digest in digests.items():
                    with archive.open(name) as stream:
                        if sha256_stream(stream) != digest:
                            raise ValueError("An exported file changed during the snapshot")
            target = dest / (bundle_id + ".zip")
            os.chmod(zipped, 0o600)
            zipped.replace(target)
            try:
                ctx.store.append(
                    ev.ProgramBundleExported(
                        project_id=controls.ws.project_id,
                        program_id=pid,
                        bundle_id=bundle_id,
                        material_version_ids=[m["id"] for m in summary["materials"]],
                        session_bundle_ids=[item[1] for item in session_exports],
                        manifest_hash=sha256_bytes(manifest),
                    ),
                    actor=Actor.USER,
                    session_id=None,
                    causation_id=ctx.requested_event_id,
                )
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return ToolResult.text(bundle_id)

    tool = ToolSpec[None](
        "export_program_bundle",
        "Export the selected research line.",
        pack,
        PermissionClass.WRITE_FS,
        args_model=Export,
    )
    bundle_id = await invoke_project(controls, tool, {"program_id": pid})
    return controls.ws.state_dir / "exports" / (bundle_id + ".zip")
