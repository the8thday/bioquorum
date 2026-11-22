"""Materialize a public offline example as a normal, resumable workbench project."""

import argparse
import asyncio
import shutil
from pathlib import Path

from evals.v10_scenario_d import seed

from bioquorum.application import artifacts, demo, materials, programs, recompute, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.application.environment import project_environment
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.review.checks import deterministic_checks
from bioquorum.review.evidence import ClaimInput, Evidence
from bioquorum.review.service import review
from bioquorum.tools.builtin.files import READ_FILE
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE
from bioquorum.tools.invoke import invoke

DATA = Path(__file__).with_name("data")
SCENARIOS = {
    "target": (
        "TYK2 in plaque psoriasis",
        "Assess TYK2 as a target in psoriasis using the POETYK PSO-1 trial.",
        ("pso1.csv",),
        (
            (
                ClaimKind.NUMERIC,
                "The unadjusted difference between published rounded PASI response rates was {{pasi_gap}} percentage points for deucravacitinib versus placebo.",
            ),
            (
                ClaimKind.CITATION,
                'The trial reported higher responses [@PMID:35820547 "response rates were significantly higher with deucravacitinib versus placebo or apremilast"].',
            ),
            (ClaimKind.PLAN, "Long-term comparative safety requires additional evidence."),
        ),
    ),
    "omics": (
        "GSE96583 paired pseudobulk",
        "Does each donor have one stimulated and one control sample in the prefiltered matrix?",
        ("paired-samples.csv", "paired-counts.csv"),
        (
            (
                ClaimKind.NUMERIC,
                "The matrix has {{genes}} distinct genes and {{donors}} paired donors.",
            ),
            (
                ClaimKind.PLAN,
                "This check establishes the pairing of columns. Differential expression requires a separate statistical analysis.",
            ),
        ),
    ),
}


async def prepare(scenario: str, root: Path, *, locked: bool = False) -> str:
    root = root.resolve()
    env_files = {"pixi.toml", "pixi.lock", ".pixi"} if locked else set()
    if root.exists() and any(p.name not in env_files for p in root.iterdir()):
        raise ValueError("Choose a new or empty project directory.")
    if locked:
        if scenario not in SCENARIOS:
            raise ValueError("Only target and omics accept a locked environment")
        project_environment(root, use_pixi=True)
        if not (root / "pixi.toml").is_file():
            raise ValueError("The locked pixi environment needs a pixi.toml manifest")
    if scenario == "review":
        controls = demo.make_demo(root)
        sid = controls.new_session("Metformin evidence review")
        controls.submit(sid, demo.QUESTION)
        await controls.tasks[sid].task
        if controls.require_session(sid)[-1].payload["outcome"] != "completed":
            raise RuntimeError("The recorded review did not finish")
        return sid
    if scenario == "correction":
        controls = await seed(root)
        pid = programs.list_programs(controls)[0]["id"]
        sid = programs.show(controls, pid)["sessions"][0]["id"]
        original = materials.catalog(controls, pid)[0]["id"]

        async def corrected():
            yield (root / "deliveries/corrected/sample-labels.csv").read_bytes()

        uploaded = await artifacts.upload(
            controls, sid, "sample-labels.csv", Classification.PUBLIC, corrected()
        )
        replacement = await materials.supersede(
            controls, pid, original, uploaded["id"], "Corrected delivery", "Two labels were swapped"
        )
        result = await recompute.start(controls, pid, original, replacement["id"])
        if result["status"] != "completed":
            raise RuntimeError("Corrected measurements could not be recomputed")
        return result["session_id"]

    title, question, inputs, statements = SCENARIOS[scenario]
    workspace.init_project(root, name=title, classification=Classification.PUBLIC)
    (root / "bioquorum.toml").write_text(
        f"[kernel]\nmacos_sandbox = true\nnetwork = false\npixi = {str(locked).lower()}\n"
    )
    ws = workspace.load(root, global_config=root / ".bioquorum/unused.toml")
    controls = ResearchControls(ws)
    sid = controls.new_session(title)
    store = controls.store
    plan_id = new_id()
    req = store.append(
        ev.TurnRequested(question=question, plan_id=plan_id), session_id=sid, actor=Actor.USER
    )
    store.append(ev.UserMessage(message=Message.user(question)), session_id=sid, actor=Actor.USER)
    store.append(
        ev.PlanProposed(
            plan_id=plan_id, steps=["Inspect the saved inputs", "Calculate the named result"]
        ),
        session_id=sid,
        actor=Actor.AGENT,
    )
    store.append(ev.TurnStarted(turn_id=req.id), session_id=sid, actor=Actor.SYSTEM)
    policy = workspace.default_permissions(ws, allow=[], servers=[])
    ctx = workspace.tool_context(ws, store, sid, policy)
    try:
        for name in inputs:
            shutil.copyfile(DATA / name, root / name)
        if scenario == "target":
            name = "pso1-source.txt"
            shutil.copyfile(DATA / name, root / name)
            source = await invoke(READ_FILE, {"path": name}, ctx)
            if source.is_error:
                raise RuntimeError(source.text())
        store.append(
            ev.PlanStepDone(plan_id=plan_id, step_index=0), session_id=sid, actor=Actor.AGENT
        )
        result = await invoke(
            PYTHON_EXECUTE,
            {"code": (DATA / f"{scenario}.py").read_text(), "input_files": list(inputs)},
            ctx,
        )
        if result.is_error:
            raise RuntimeError(result.text())
        store.append(
            ev.PlanStepDone(plan_id=plan_id, step_index=1), session_id=sid, actor=Actor.AGENT
        )
        answer = store.append(
            ev.AssistantMessage(message=Message.assistant("\n\n".join(t for _, t in statements))),
            session_id=sid,
            actor=Actor.AGENT,
        )
        await review(
            store,
            session_id=sid,
            target_event_id=answer.id,
            claims=[ClaimInput(i, t, kind) for i, (kind, t) in enumerate(statements, 1)],
            evidence=Evidence.from_events(store.history(sid)),
            checks=deterministic_checks(),
            classification=Classification.PUBLIC,
        )
        outcome = "completed"
    except BaseException:
        outcome = "failed"
        raise
    finally:
        store.append(
            ev.TurnFinished(turn_id=req.id, outcome=outcome), session_id=sid, actor=Actor.SYSTEM
        )
        await ctx.require_kernels().shutdown_all()
    return sid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=[*SCENARIOS, "review", "correction"])
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--locked", action="store_true", help="Use the pre-provisioned project pixi environment"
    )
    args = parser.parse_args()
    print(asyncio.run(prepare(args.scenario, args.project, locked=args.locked)))
