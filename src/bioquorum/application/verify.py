"""Rerun and compare recorded computations; compare independent studies after completion."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from bioquorum.agent.budget import Budget
from bioquorum.application import research, workspace
from bioquorum.application.environment import project_environment
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ModelRole, PermissionClass, PermissionScope
from bioquorum.core.hashing import code_hash, sha256_file
from bioquorum.core.ids import new_id
from bioquorum.core.refs import NamedValue, QuoteRef, ValueRef
from bioquorum.core.status import StatusTrigger, VerificationStatus, transition
from bioquorum.core.verification import Tolerance, VerificationOutcome, compare_values
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.policy.secrets import redact
from bioquorum.runtime.manager import KernelManager, jupyter_factory
from bioquorum.runtime.sandbox import SandboxSpec, bwrap_available
from bioquorum.tools import ToolContext, ToolRegistry
from bioquorum.tools.builtin.files import LIST_FILES, READ_FILE
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke
from bioquorum.tools.replay import recorded_inputs, replay_spec, run_chain

if TYPE_CHECKING:
    from bioquorum.application.controls import ResearchControls


@dataclass
class Study:
    claim: ev.ClaimExtracted
    status: VerificationStatus
    runs: list[tuple[ev.RunStarted, ev.RunFinished]]
    targets: list[NamedValue]


class BlindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    definitions: dict[str, str]
    tolerance: Tolerance = Field(default_factory=Tolerance)
    max_estimated_usd: float = Field(gt=0, le=100, allow_inf_nan=False)


def _runs(events: list[ev.Event]) -> dict[str, tuple[ev.RunStarted, ev.RunFinished]]:
    starts: dict[str, ev.RunStarted] = {}
    result = {}
    for event in events:
        data = event.parsed()
        if isinstance(data, ev.RunStarted):
            starts[data.run_id] = data
        elif isinstance(data, ev.RunFinished) and data.run_id in starts:
            result[data.run_id] = (starts[data.run_id], data)
    return result


def _study(controls: ResearchControls, sid: str, claim_id: str) -> Study:
    local = controls.require_session(sid)
    history = controls.store.history(sid)
    status = VerificationStatus.UNVERIFIED
    claim: ev.ClaimExtracted | None = None
    for event in local:
        data = event.parsed()
        if isinstance(data, ev.ClaimExtracted) and data.claim_id == claim_id:
            claim = data
        elif isinstance(data, ev.ClaimStatusChanged) and data.claim_id == claim_id:
            status = data.to_status
    if claim is None:
        raise LookupError("Claim not found in this session")
    refs = [(ref.run_id, ref.name) for ref in claim.refs if isinstance(ref, ValueRef)]
    if any(isinstance(ref, QuoteRef) for ref in claim.refs):
        raise ValueError(
            "Verify the numerical result as a separate claim from its literature quote"
        )
    if not refs:
        raise ValueError("This claim has no bound calculation")
    runs = _runs(history)
    values: dict[str, NamedValue] = {}
    for run_id, name in refs:
        if run_id not in runs:
            raise ValueError("The claim refers to an unavailable calculation")
        original = next((v for v in runs[run_id][1].values if v.name == name), None)
        if original is None:
            raise ValueError("The claim refers to an unavailable named result")
        if name in values and values[name] != original:
            raise ValueError("This claim binds different definitions or values to the same name")
        values[name] = original
    ids = list(dict.fromkeys(run_id for run_id, _ in refs))
    return Study(claim, status, [runs[run_id] for run_id in ids], list(values.values()))


def _change(
    controls: ResearchControls,
    sid: str,
    study: Study,
    result: ev.Event,
    trigger: StatusTrigger,
    operation: str,
) -> None:
    status = transition(study.status, trigger)
    if status is not study.status:
        controls.store.append(
            ev.ClaimStatusChanged(
                claim_id=study.claim.claim_id,
                from_status=study.status,
                to_status=status,
                trigger=trigger,
                reason=trigger.value,
                rerun_id=operation,
            ),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=result.id,
        )


def _validate_run(
    start: ev.RunStarted, end: ev.RunFinished, *, lock: str | None, target: bool
) -> None:
    if start.synthetic:
        raise ValueError(
            "This calculation was replayed from a fixture. Run it in a real kernel first."
        )
    if start.language != "python" or start.code_hash != code_hash(start.code):
        raise ValueError("The recorded Python code could not be verified")
    if not start.environment or start.env_lock_hash != lock:
        raise ValueError("The original environment is unknown or its lock has changed")
    if target and not (end.values or end.outputs or end.stdout_ref):
        raise ValueError("The recorded run has no comparable output")


def _equivalent(original: ev.RunFinished, replay: ev.RunFinished) -> bool:
    def digest(ref):  # the content is compared, never a machine-local filename
        return ref.content_hash if ref else None

    return (
        original.run_key == replay.run_key
        and [digest(ref) for ref in original.outputs] == [digest(ref) for ref in replay.outputs]
        and digest(original.stdout_ref) == digest(replay.stdout_ref)
        and {v.name: (v.value, v.unit) for v in original.values}
        == {v.name: (v.value, v.unit) for v in replay.values}
    )


async def rerun(controls: ResearchControls, sid: str, claim_id: str) -> dict:
    controls._ensure_idle()
    study = _study(controls, sid, claim_id)
    if study.status not in (
        VerificationStatus.REVIEWED,
        VerificationStatus.REPRODUCED,
        VerificationStatus.REPLICATED,
    ):
        raise ValueError("Review the claim before rerunning its calculations")
    controls.reviewing = True
    rerun_id = new_id()
    started = controls.store.append(
        ev.RerunStarted(
            rerun_id=rerun_id, claim_id=claim_id, run_ids=[start.run_id for start, _ in study.runs]
        ),
        session_id=sid,
        actor=Actor.USER,
    )
    outcomes: list[dict] = []
    try:
        ws = controls.ws
        python, lock = project_environment(
            ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi"))
        )
        policy = PermissionPolicy()
        policy.grant(PermissionClass.EXECUTE, "python", PermissionScope.SESSION, session_id=sid)
        cas = ws.cas()
        history = controls.store.history(sid)
        recorded = _runs(history)
        for original, expected in study.runs:
            row: dict = {"original_run_id": original.run_id, "outcome": "inconclusive"}
            try:
                chain = run_chain(history, original)
                for before in chain:
                    end = recorded[before.run_id][1]
                    _validate_run(before, end, lock=lock, target=before.run_id == original.run_id)
                    for ref in [*end.outputs, *([end.stdout_ref] if end.stdout_ref else [])]:
                        if sha256_file(cas.path(ref.content_hash)) != ref.content_hash:
                            raise ValueError("An original output failed its integrity check")
                with TemporaryDirectory(prefix="bq-replay-") as temp:
                    root = Path(temp).resolve()
                    manager = KernelManager(
                        controls.kernel_factory
                        or jupyter_factory(
                            root,
                            sandbox=SandboxSpec(
                                workdir=root,
                                read_only=tuple(root / name for name in recorded_inputs(chain)),
                            ),
                            require_isolation=ws.isolation_required,
                            python_executable=python,
                        )
                    )
                    ctx = ToolContext(
                        store=controls.store,
                        session_id=sid,
                        permissions=policy,
                        project_id=ws.project_id,
                        classification=ws.classification,
                        cas=cas,
                        kernels=manager,
                        workdir=root,
                        env_lock_hash=lock,
                        verification_run=True,
                        actor=Actor.USER,
                    )
                    try:
                        result = await invoke(replay_spec(chain), {}, ctx, causation_id=started.id)
                    finally:
                        await manager.shutdown_all()
                if result.is_error:
                    raise ValueError("The calculation did not finish in the fresh kernel")
                observed_runs = _runs(controls.store.read(sid))
                pairs = [
                    (before, recorded[before.run_id][1], *observed_runs[after_id])
                    for before, after_id in zip(chain, json.loads(result.text()), strict=True)
                ]
                actual, observed = pairs[-1][2:]
                row["rerun_id"] = actual.run_id
                if any(
                    after.synthetic or after.environment != before.environment
                    for before, _, after, _ in pairs
                ):
                    raise ValueError("The fresh kernel does not match the recorded environment")
                if any(after.isolation != before.isolation for before, _, after, _ in pairs):
                    raise ValueError("The fresh kernel does not match the recorded isolation mode")
                row["values"] = [
                    v.model_dump(mode="json")
                    for v in compare_values(expected.values, observed.values, Tolerance())
                ]
                row["cells_compared"] = len(pairs)
                row["outcome"] = (
                    "matched"
                    if all(_equivalent(before, after) for _, before, _, after in pairs)
                    else "different"
                )
                row["environment"] = "pixi-lock-and-inventory" if lock else "local-inventory"
                row["certifiable"] = bool(
                    lock and all(after.isolation == "bwrap" for _, _, after, _ in pairs)
                )
            except (ValueError, LookupError, OSError) as exc:
                row["reason"] = redact(str(exc))[:500]
            outcomes.append(row)
    except (ValueError, OSError) as exc:
        outcomes.append({"outcome": "inconclusive", "reason": redact(str(exc))[:500]})
    finally:
        states = {row["outcome"] for row in outcomes}
        verdict = (
            "different"
            if "different" in states
            else "matched"
            if states == {"matched"}
            else "inconclusive"
        )
        eligible = bool(outcomes) and all(row.get("certifiable") for row in outcomes)
        details = {
            "claim_id": claim_id,
            "outcome": verdict,
            "runs": outcomes,
            "status_eligible": eligible,
        }
        event = controls.store.append(
            ev.RerunFinished(rerun_id=rerun_id, matched=verdict == "matched", details=details),
            session_id=sid,
            actor=Actor.SYSTEM,
            causation_id=started.id,
        )
        controls.reviewing = False
    if verdict != "inconclusive" and eligible:
        trigger = (
            StatusTrigger.RERUN_MATCHED if verdict == "matched" else StatusTrigger.RERUN_MISMATCHED
        )
        _change(controls, sid, study, event, trigger, rerun_id)
    return details


def _pricing(controls: ResearchControls, sid: str) -> dict:
    client = controls.make_client(controls.ws, controls.store)
    routes = client.router.chain(ModelRole.WORKER, controls.ws.classification)
    if not routes or any(str(route.spec) not in client.prices for route in routes):
        raise ValueError("Configure input and output rates for every eligible worker route first")
    prices = [client.prices[str(route.spec)] for route in routes]
    base = (
        24_000 * max(rate[0] for rate in prices) + 6_000 * max(rate[1] for rate in prices)
    ) / 1e6
    return {
        "estimated_usd": base,
        "basis": "24,000 input and 6,000 output tokens; actual costs can differ",
    }


def _blind_inputs(controls: ResearchControls, sid: str, study: Study):
    history = controls.store.history(sid)
    return {
        "inputs/" + name: ref
        for name, ref in recorded_inputs(
            cell for original, _ in study.runs for cell in run_chain(history, original)
        ).items()
    }


def replication_quote(controls: ResearchControls, sid: str, claim_id: str) -> dict:
    study = _study(controls, sid, claim_id)
    if study.status not in (VerificationStatus.REPRODUCED, VerificationStatus.REPLICATED):
        raise ValueError("Reproduce the claim before requesting independent replication")
    original = [run for run, _ in study.runs]
    if any(run.synthetic for run in original):
        raise ValueError("A fixture cannot become an independently verified claim")
    files = _blind_inputs(controls, sid, study)
    return {
        **_pricing(controls, sid),
        "targets": [{"name": v.name, "unit": v.unit} for v in study.targets],
        "inputs": [{"path": path, "sha256": ref.content_hash} for path, ref in files.items()],
        "isolated_execution_available": bwrap_available(),
    }


async def replicate(
    controls: ResearchControls, sid: str, claim_id: str, request: BlindRequest
) -> dict:
    controls._ensure_idle()
    quote = replication_quote(controls, sid, claim_id)
    study = _study(controls, sid, claim_id)
    if not quote["isolated_execution_available"]:
        raise ValueError("Blind replication requires a Linux kernel sandbox")
    if request.max_estimated_usd < quote["estimated_usd"]:
        raise ValueError("The estimate exceeds the approved budget")
    question = request.question.strip()
    definitions = {k: v.strip() for k, v in request.definitions.items()}
    if (
        not question
        or set(definitions) != {v.name for v in study.targets}
        or any(not definition or len(definition) > 500 for definition in definitions.values())
    ):
        raise ValueError("Supply a neutral question and one definition per named result")
    inputs = _blind_inputs(controls, sid, study)
    ws = controls.ws
    python, _lock = project_environment(
        ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi"))
    )
    operation = new_id()
    blind_sid = workspace.start_session(
        ws, controls.store, f"Independent calculation {operation[-6:]}"
    )
    controls.reviewing = True
    started = controls.store.append(
        ev.ReplicationStarted(
            replication_id=operation,
            claim_id=claim_id,
            blind_session_id=blind_sid,
            question=question,
            definitions=definitions,
            tolerance=request.tolerance,
            estimated_usd=quote["estimated_usd"],
            max_estimated_usd=request.max_estimated_usd,
        ),
        session_id=sid,
        actor=Actor.USER,
    )
    comparisons = []
    outcome: VerificationOutcome = "inconclusive"
    note = ""
    try:
        targets = "\n".join(
            f"- {v.name} ({v.unit or 'unitless'}): {definitions[v.name]}" for v in study.targets
        )
        required = "\n".join(f"- {name}" for name in inputs)
        message = f"{question}\n\nInput files:\n{required or '(none)'}\n\nDeclare these results using bq.value:\n{targets}"
        with TemporaryDirectory(prefix="bq-blind-") as tmp:
            root = Path(tmp).resolve()
            stage = ToolContext(
                store=controls.store,
                session_id=blind_sid,
                permissions=PermissionPolicy.allow_all(),
                cas=ws.cas(),
                classification=ws.classification,
                actor=Actor.USER,
                workdir=root,
            )
            from bioquorum.tools.replay import NoArguments, stage_inputs
            from bioquorum.tools.spec import ToolResult, ToolSpec

            async def materialize(args, ctx):
                stage_inputs(root, ctx.require_cas(), inputs)
                return ToolResult.text("Inputs staged")

            staged = await invoke(
                ToolSpec(
                    "stage_blind_inputs",
                    "Restore only the predeclared inputs.",
                    materialize,
                    PermissionClass.WRITE_FS,
                    args_model=NoArguments,
                ),
                {},
                stage,
            )
            if staged.is_error:
                raise ValueError("Unable to restore one or more recorded inputs")
            disposable = dataclasses.replace(
                ws,
                root=root,
                data_dirs=[],
                config={
                    "agent": {"binding": False},
                },
            )
            await research.execute(
                disposable,
                controls.store,
                blind_sid,
                message,
                client=controls.make_client(ws, controls.store),
                max_steps=6,
                budget=Budget(usd=request.max_estimated_usd),
                permissions=lambda servers: workspace.default_permissions(
                    disposable, allow=[], servers=[]
                ),
                kernel_factory=jupyter_factory(
                    root,
                    sandbox=SandboxSpec(
                        workdir=root,
                        read_only=tuple(root / name for name in inputs),
                        hidden=(root / workspace.STATE_DIR,),
                    ),
                    require_isolation=True,
                    python_executable=python,
                ),
                registry_factory=lambda: ToolRegistry([PYTHON_EXECUTE, LIST_FILES, READ_FILE]),
            )
            expected_inputs = {ref.content_hash for ref in inputs.values()}
            produced: dict[str, NamedValue] = {}
            blind_history = controls.store.read(blind_sid)
            for start, end in _runs(blind_history).values():
                if start.synthetic or start.isolation != "bwrap":
                    continue
                try:
                    chain = run_chain(blind_history, start)
                except ValueError:
                    continue
                seen_inputs = {ref.content_hash for cell in chain for ref in cell.inputs}
                if expected_inputs.issubset(seen_inputs):
                    produced.update({v.name: v for v in end.values})
        comparisons = compare_values(study.targets, list(produced.values()), request.tolerance)
        if any(row.matched is False for row in comparisons):
            outcome = "different"
        elif all(row.matched is True for row in comparisons):
            outcome = "matched"
        else:
            note = "Some named results were missing from runs that declared all the inputs."
    except Exception as exc:
        note = redact(str(exc))[:500]
    finally:
        finished = ev.ReplicationFinished(
            replication_id=operation,
            claim_id=claim_id,
            outcome=outcome,
            comparisons=comparisons,
            note=note,
        )
        event = controls.store.append(
            finished, session_id=sid, actor=Actor.SYSTEM, causation_id=started.id
        )
        controls.reviewing = False
    if outcome != "inconclusive":
        trigger = (
            StatusTrigger.REPLICATION_MATCHED
            if outcome == "matched"
            else StatusTrigger.REPLICATION_MISMATCHED
        )
        _change(controls, sid, study, event, trigger, operation)
    return finished.model_dump(mode="json")


async def export_bundle(controls: ResearchControls, sid: str) -> dict:
    from bioquorum.review.evidence import Evidence
    from bioquorum.tools.bundle import export_spec

    controls._ensure_idle()
    controls.require_session(sid)
    ws = controls.ws
    policy = PermissionPolicy()
    policy.grant(
        PermissionClass.WRITE_FS, "research-bundle", PermissionScope.SESSION, session_id=sid
    )
    evidence = Evidence.from_events(controls.store.history(sid))
    ctx = ToolContext(
        store=controls.store,
        session_id=sid,
        permissions=policy,
        classification=ws.classification,
        cas=ws.cas(),
        workdir=ws.root,
        actor=Actor.USER,
        project_id=ws.project_id,
    )
    controls.reviewing = True
    try:
        result = await invoke(
            export_spec(ws.name, {s.call_id: s.text for s in evidence.sources}), {}, ctx
        )
    finally:
        controls.reviewing = False
    if result.is_error:
        raise ValueError(result.text())
    bundle_id = result.text()
    path = bundle_file(controls, sid, bundle_id)
    return {"id": bundle_id, "size": path.stat().st_size}


def bundle_file(controls: ResearchControls, sid: str, bundle_id: str) -> Path:
    events = controls.require_session(sid)
    for e in reversed(events):
        payload = e.parsed()
        if isinstance(payload, ev.BundleExported) and payload.bundle_id == bundle_id:
            name = f"{bundle_id}.zip"
            if payload.path == name and Path(name).name == name:
                path = controls.ws.state_dir / "exports" / name
                if path.is_file() and not path.is_symlink():
                    return path
    raise LookupError("Research bundle not found in this session")
