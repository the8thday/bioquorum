"""Inspect the scope of a correction, then explicitly recompute it in fresh kernels."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

from bioquorum.application import materials, programs
from bioquorum.application.controls import ResearchControls
from bioquorum.application.environment import project_environment
from bioquorum.application.program_lineage import downstream
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.core.refs import DataRef, ValueRef
from bioquorum.core.status import StatusTrigger, VerificationStatus
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.policy.secrets import redact
from bioquorum.review.binding import ground_claim
from bioquorum.review.evidence import Evidence
from bioquorum.review.recompute import measurement_statement
from bioquorum.storage import materials as stored
from bioquorum.storage import programs as projected
from bioquorum.tools import ToolContext, ToolResult, ToolSpec, invoke
from bioquorum.tools.recompute import CellChain, corrected_replay
from bioquorum.tools.replay import NoArguments, dependency_chain, recorded_inputs, recorded_writes


def _operation_rows(controls: ResearchControls, pid: str, vid: str, impact: dict) -> list[dict]:
    operations = {}
    records = controls.store.read(None, types=("recompute_started", "recompute_finished"))
    for event in records:
        p = event.parsed()
        if not isinstance(p, ev.RecomputeStarted | ev.RecomputeFinished) or (
            p.program_id,
            p.material_version_id,
            p.project_id,
        ) != (pid, vid, controls.ws.project_id):
            continue
        if isinstance(p, ev.RecomputeStarted):
            operations[p.recompute_id] = dict(
                id=p.recompute_id,
                session_id=p.session_id,
                replacement_version_id=p.replacement_version_id,
                run_ids=p.run_ids,
                claim_ids=p.claim_ids,
                status="running",
                reason="",
                comparisons=[],
                repair_prompt="",
            )
        elif p.recompute_id in operations:
            row = operations[p.recompute_id]
            row.update(status=p.status, reason=p.reason)
            if p.status == "failed":
                replacement = stored.version(
                    controls.store.db, controls.ws.project_id, pid, row["replacement_version_id"]
                )
                uploaded = stored.find_upload(
                    controls.store.db, controls.ws.project_id, replacement["file_id"]
                )[1]
                row["repair_prompt"] = (
                    "Inspect the failed recalculation and its recorded kernel error. "
                    "Propose the smallest code or input change and explain the scientific consequence before applying it. "
                    f"Original material version {vid}, replacement {row['replacement_version_id']}. "
                    f"Use the replacement upload {uploaded.path} (SHA-256 {replacement['hash']}) for the corrected calculation. "
                    f"Recorded failure: {p.reason}"
                )
            else:
                row["comparisons"] = _comparison_rows(controls, p, impact, row["session_id"])
    return list(operations.values())


def _comparison_rows(
    controls: ResearchControls, finished: ev.RecomputeFinished, impact: dict, sid: str
) -> list[dict]:
    old_claims = {r["id"]: r for r in impact["claims"]}
    original = {(v["run_id"], v["name"]): v for v in impact["values"]}
    history = controls.store.read(sid)
    revised = {row["id"]: row for row in programs._current_claims(controls, sid)}
    actual = {
        (p.run_id, v.name): v
        for e in history
        if isinstance(p := e.parsed(), ev.RunFinished)
        for v in p.values
    }
    members = frozenset(
        row["id"]
        for row in projected.show(controls.store.db, controls.ws.project_id, finished.program_id)[
            "sessions"
        ]
    )
    sessions = {}
    rows = []
    for pair in finished.comparisons:
        old = old_claims.get(pair.old_claim_id)
        if old is None:
            continue
        if old["session_id"] not in sessions:
            sessions[old["session_id"]] = {
                r["id"]: r for r in programs._current_claims(controls, old["session_id"], members)
            }
        entry = sessions[old["session_id"]].get(pair.old_claim_id, old)
        claim_event = controls.store.get(old["event_id"])
        claim = claim_event.parsed() if claim_event else None
        values = []
        if isinstance(claim, ev.ClaimExtracted):
            for ref in claim.refs:
                if (
                    isinstance(ref, ValueRef)
                    and (before := original.get((ref.run_id, ref.name))) is not None
                ):
                    after = actual.get((finished.run_map.get(ref.run_id, ""), ref.name))
                    values.append(
                        dict(
                            name=ref.name,
                            old_value=before["value"],
                            new_value=after.value if after else None,
                            unit=before["unit"],
                            new_unit=after.unit if after else None,
                        )
                    )
        corrected = revised.get(pair.new_claim_id)
        rows.append(
            dict(
                old_claim_id=pair.old_claim_id,
                new_claim_id=pair.new_claim_id,
                review_cleared=pair.review_cleared,
                old_text=entry["text"],
                old_status=entry.get("status", "unverified"),
                new_text=corrected["text"] if corrected else None,
                new_status=corrected["status"] if corrected else None,
                values=values,
            )
        )
    return rows


def preview(controls: ResearchControls, pid: str, vid: str) -> dict:
    versions = materials.catalog(controls, pid)
    old = next((v for v in versions if v["id"] == vid), None)
    if not old:
        raise LookupError("Material version not found in this research line")
    active = next(
        (
            v
            for v in versions
            if v["material_id"] == old["material_id"]
            and v["status"] == "active"
            and v["version"] > old["version"]
        ),
        None,
    )
    impact = downstream(controls, pid, vid)
    operations = _operation_rows(controls, pid, vid, impact)
    reason = ""
    if not active:
        reason = "A later active version of this material is required."
    elif impact.get("ambiguous_inputs"):
        reason = "One or more input snapshots match multiple material versions without a recorded origin."
    elif not impact["runs"]:
        reason = "No recorded calculations are downstream of this material version."
    elif any(op["status"] == "running" for op in operations):
        reason = "A recalculation is already running."
    elif projected_closed(controls, pid):
        reason = "The research line is closed."
    return dict(
        old_version_id=vid,
        replacement_version_id=active["id"] if active else None,
        impact=impact,
        estimated_model_usd=0,
        estimate_basis="Recorded code runs on local kernels without model calls. Wall time and local compute usage depend on the cells.",
        can_recompute=not reason,
        reason=reason,
        operations=operations,
    )


def projected_closed(controls: ResearchControls, pid: str) -> bool:
    return not any(
        row["id"] == pid and row["status"] == "active" for row in programs.list_programs(controls)
    )


def _chains(controls: ResearchControls, pid: str, impact: dict) -> list[CellChain]:
    # Sessions are separate kernel namespaces. Preserve recorded event order, including
    # cross-session producer/consumer order, or report an interleaving we cannot stage.
    by_session: dict[str, set[str]] = defaultdict(set)
    for row in impact["runs"]:
        by_session[row["session_id"]].add(row["id"])
    chains: dict[str, CellChain] = {}
    owners: dict[str, str] = {}
    members = frozenset(
        row["id"]
        for row in projected.show(controls.store.db, controls.ws.project_id, pid)["sessions"]
    )
    for sid, targets in by_session.items():
        history = controls.store.history(sid, members=members)
        starts = {p.run_id: p for e in history if isinstance(p := e.parsed(), ev.RunStarted)}
        required = {
            cell.run_id for rid in targets for cell in dependency_chain(history, starts[rid])
        }
        writes = recorded_writes(history)
        chains[sid] = CellChain([p for key, p in starts.items() if key in required], writes)
        owners.update(dict.fromkeys(required, sid))

    # Identify the actual earlier producer at the time each cell read its input.
    # Identical historical bytes produced at another time do not imply the same correction.
    events = projected.event_log(controls.store.db, controls.ws.project_id, pid)
    latest: dict[tuple[str | None, str], tuple[str, str]] = {}
    isolated = {e.payload["session_id"] for e in events if e.type == ev.RecomputeStarted.event_type}
    scopes: dict[str, str | None] = {}
    writes = recorded_writes(events)
    deps: dict[str, set[str]] = {sid: set() for sid in chains}
    for event in events:
        payload = event.parsed()
        if isinstance(payload, ev.RunFinished):
            for name, digest in writes.get(payload.run_id, {}).items():
                latest[scopes.get(payload.run_id), name] = (payload.run_id, digest)
        elif isinstance(payload, ev.RunStarted):
            scope = (
                event.session_id
                if payload.verification_run or event.session_id in isolated
                else None
            )
            scopes[payload.run_id] = scope
            sid = owners.get(payload.run_id)
            if sid is None:
                continue
            for name, ref in recorded_inputs([payload]).items():
                previous = latest.get((scope, name))
                if not previous or previous[1] != ref.content_hash:
                    continue
                owner = owners.get(previous[0])
                if owner is not None and owner != sid:
                    if name in chains[sid].producers and chains[sid].producers[name] != previous[0]:
                        raise ValueError(
                            "Two different cross-session producers rewrote the same input path"
                        )
                    chains[sid].producers[name] = previous[0]
                    deps[sid].add(owner)
    try:
        return [chains[sid] for sid in TopologicalSorter(deps).static_order()]
    except CycleError:
        raise ValueError(
            "The recorded sessions interleave file writes; inspect their cell order before recalculating"
        ) from None


def _new_claims(
    controls: ResearchControls,
    op: ev.RecomputeStarted,
    impact: dict,
    run_map: dict[str, str],
    start_event_id: str,
) -> tuple[list[tuple[str | None, ev.EventPayload]], list[ev.RecomputedClaim]]:
    sid = op.session_id
    evidence = Evidence.from_events(controls.store.read(sid))
    old_values = {(r["run_id"], r["name"]): r for r in impact["values"]}
    claims = []
    pairs = []
    for row in impact["claims"]:
        source = controls.store.get(row["event_id"])
        old = source.parsed() if source else None
        pair = ev.RecomputedClaim(old_claim_id=row["id"])
        pairs.append(pair)
        if not isinstance(old, ev.ClaimExtracted) or old.kind is not ClaimKind.NUMERIC:
            continue
        refs = [r for r in old.refs if isinstance(r, ValueRef)]
        if not refs or len(refs) != len(old.refs):
            continue
        measured = []
        for ref in refs:
            actual = evidence.values.get(ref.name)
            before = old_values.get((ref.run_id, ref.name))
            if (
                actual is None
                or before is None
                or actual.run_id != run_map.get(ref.run_id)
                or actual.value.unit != before["unit"]
            ):
                break
            measured.append(
                f"{ref.name} = {{{{{ref.name}}}}}"
                + (f" {actual.value.unit}" if actual.value.unit else "")
            )
        else:
            # Rebind only measured quantities. Old biological interpretations may be
            # invalid after the correction and must never be copied to the new record.
            template = "; ".join(measured)
            grounding = ground_claim(template, evidence)
            if grounding.state == "grounded" and set(
                (r.run_id, r.name) for r in grounding.refs if isinstance(r, ValueRef)
            ) == {(run_map[r.run_id], r.name) for r in refs}:
                new_claim_id = new_id()
                pure = measurement_statement(
                    old.text, [evidence.values[ref.name].value for ref in refs]
                )
                pairs[-1] = ev.RecomputedClaim(
                    old_claim_id=old.claim_id, new_claim_id=new_claim_id, review_cleared=pure
                )
                claims.append((old.claim_id, new_claim_id, template, grounding.refs, pure))
    entries: list[tuple[str | None, ev.EventPayload]] = []
    if claims:
        answer = controls.store.append(
            ev.AssistantMessage(
                message=Message.assistant(
                    "Recalculated measurements\n\n" + "\n\n".join(c[2] for c in claims)
                )
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=start_event_id,
        )
        for old_id, new_claim, template, refs, pure in claims:
            entries.extend(
                [
                    (
                        sid,
                        ev.ClaimExtracted(
                            claim_id=new_claim,
                            source_event_id=answer.id,
                            text=template,
                            kind=ClaimKind.NUMERIC,
                            refs=list(refs),
                        ),
                    ),
                    (
                        sid,
                        ev.ClaimStatusChanged(
                            claim_id=new_claim,
                            from_status=VerificationStatus.UNVERIFIED,
                            to_status=VerificationStatus.GROUNDED,
                            trigger=StatusTrigger.BINDINGS_RESOLVED,
                        ),
                    ),
                ]
            )
            if pure:
                entries.append(
                    (
                        None,
                        ev.ClaimReviewCleared(
                            project_id=op.project_id,
                            program_id=op.program_id,
                            material_version_id=op.material_version_id,
                            claim_id=old_id,
                            replacement_claim_id=new_claim,
                            recompute_id=op.recompute_id,
                        ),
                    )
                )
    return entries, pairs


async def _execute(
    controls: ResearchControls,
    pid: str,
    vid: str,
    replacement: str,
    report: dict,
    ctx: ToolContext[None],
) -> str:
    ws = controls.ws
    sid, operation = new_id(), new_id()
    scope = dict(project_id=ws.project_id, program_id=pid, material_version_id=vid)
    impact = report["impact"]
    started = ev.RecomputeStarted(
        **scope,
        session_id=sid,
        recompute_id=operation,
        replacement_version_id=replacement,
        run_ids=[r["id"] for r in impact["runs"]],
        claim_ids=[r["id"] for r in impact["claims"]],
    )
    launch_events = controls.store.append_streams(
        [
            (
                sid,
                ev.SessionCreated(
                    project_id=ws.project_id, title="Recalculation with corrected material"
                ),
            ),
            (None, ev.SessionAttached(project_id=ws.project_id, program_id=pid, session_id=sid)),
            (None, started),
        ],
        actor=Actor.USER,
        causation_id=ctx.requested_event_id,
    )
    updated: dict[str, str] = {}
    entries: list[tuple[str | None, ev.EventPayload]] = []
    comparisons = []
    reason = ""
    try:
        old = stored.version(controls.store.db, ws.project_id, pid, vid)
        new = stored.version(controls.store.db, ws.project_id, pid, replacement)
        path, _ = materials.download(controls, pid, replacement)
        ref = DataRef(
            uri=ws.cas().uri(new["hash"]), content_hash=new["hash"], size=path.stat().st_size
        )
        python, lock = project_environment(
            ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi"))
        )
        policy = PermissionPolicy()
        same_digest = [
            v
            for v in materials.catalog(controls, pid)
            if v["hash"] == old["hash"] and v["id"] != vid
        ]
        required_path = (
            stored.find_upload(controls.store.db, ws.project_id, old["file_id"])[1].path
            if same_digest
            else None
        )
        spec = corrected_replay(
            _chains(controls, pid, impact),
            old["hash"],
            ref,
            python,
            ws.kernel_sandbox(),
            updated,
            required_path,
        )
        policy.grant(PermissionClass.EXECUTE, spec.name, PermissionScope.SESSION, session_id=sid)
        new_ctx = ToolContext(
            store=controls.store,
            session_id=sid,
            project_id=ws.project_id,
            actor=Actor.USER,
            permissions=policy,
            classification=ws.classification,
            env_lock_hash=lock,
            cas=ws.cas(),
        )
        result = await invoke(spec, {}, new_ctx, causation_id=ctx.requested_event_id)
        if result.is_error:
            raise ValueError(result.text())
        if stored.version(controls.store.db, ws.project_id, pid, replacement)["status"] != "active":
            raise ValueError("The replacement material changed during recalculation")
        entries, comparisons = _new_claims(controls, started, impact, updated, launch_events[-1].id)
    except asyncio.CancelledError:
        reason = "Recalculation interrupted."
        raise
    except Exception as exc:
        reason = redact(str(exc))[:1000] or "Recalculation failed."
    finally:
        entries.append(
            (
                None,
                ev.RecomputeFinished(
                    **scope,
                    recompute_id=operation,
                    status="failed" if reason else "completed",
                    reason=reason,
                    run_map=updated,
                    comparisons=comparisons,
                ),
            )
        )
        controls.store.append_streams(
            entries, actor=Actor.SYSTEM, causation_id=ctx.requested_event_id
        )
    return operation


async def start(
    controls: ResearchControls, pid: str, vid: str, replacement_version_id: str
) -> dict:
    if controls.demo:
        raise ValueError("Open a research project to recalculate material")
    controls.ensure_idle()
    report = preview(controls, pid, vid)
    if not report["can_recompute"] or replacement_version_id != report["replacement_version_id"]:
        raise ValueError(report["reason"] or "The replacement version changed; refresh the preview")
    impact = report["impact"]
    for previous in reversed(report["operations"]):
        if (
            previous["replacement_version_id"] == replacement_version_id
            and previous["status"] == "completed"
            and set(previous["run_ids"]) == {r["id"] for r in impact["runs"]}
            and set(previous["claim_ids"]) == {r["id"] for r in impact["claims"]}
        ):
            return previous

    async def launch(args: NoArguments, ctx: ToolContext[None]) -> ToolResult:
        return ToolResult.text(
            await _execute(controls, pid, vid, replacement_version_id, report, ctx)
        )

    spec: ToolSpec[None] = ToolSpec(
        "recompute_material",
        "Recalculate using the selected replacement version.",
        launch,
        PermissionClass.EXECUTE,
        args_model=NoArguments,
    )
    ctx = ToolContext(
        store=controls.store,
        session_id=None,
        actor=Actor.USER,
        project_id=controls.ws.project_id,
        classification=controls.ws.classification,
        permissions=PermissionPolicy(),
    )
    ctx.permissions.grant(
        PermissionClass.EXECUTE,
        spec.name,
        PermissionScope.PROJECT,
        project_id=controls.ws.project_id,
    )
    controls.reviewing = True
    try:
        response = await invoke(spec, {}, ctx)
    finally:
        controls.reviewing = False
    if response.is_error:
        raise ValueError(response.text())
    return next(
        row for row in _operation_rows(controls, pid, vid, impact) if row["id"] == response.text()
    )
