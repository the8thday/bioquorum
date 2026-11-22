"""Run the frozen BixBench/TableQA subset through BioQuorum and save answers for scoring.

Each task gets a fresh public project holding only its inputs (BixBench tasks keep a
locked pixi environment provisioned inside the project, the documented layout). The model works through the
normal agent loop (sandboxed kernel, vision for kernel images) and must end with a line
``FINAL ANSWER: ...``. Answers, failures and token usage are saved; scoring is done by
``v04_public_subset.py --predictions`` so the grader stays independent of this runner.

    uv run python evals/v04_public_run.py --questions /data/bench/questions.json \\
        --data /data/bench/data --pixi-env /data/bench/env \\
        --worker openai:gpt-6.1-sol --reviewer deepseek:deepseek-v4-pro \\
        --output evals/results/v04/public-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from bioquorum.application import research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_file
from bioquorum.policy.secrets import redact

FINAL = re.compile(r"FINAL ANSWER:\s*(.+)", re.I)


def stage(task: dict, data: Path, root: Path, pixi_env: Path | None) -> list[str]:
    """Copy only this task's inputs into the project; return project-relative input paths."""
    staged = []
    for name in task["inputs"]:
        source = data / name
        if name.endswith(".zip"):
            target = root / "data"
            with zipfile.ZipFile(source) as archive:
                archive.extractall(target)
            staged += sorted(str(p.relative_to(root)) for p in target.iterdir())
        else:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if "image_sha256" in task and sha256_file(target) != task["image_sha256"]:
                raise ValueError(f"Staged table differs from the frozen image: {name}")
            staged.append(name)
    if pixi_env is not None and task["benchmark"].startswith("bix"):
        for name in ("pixi.toml", "pixi.lock"):
            shutil.copyfile(pixi_env / name, root / name)
        if not (root / ".pixi" / "envs" / "default").is_dir():
            raise RuntimeError(
                f"Provision the locked environment first: pixi install --locked in {root}"
            )
    return staged


KEEP = {".pixi", "pixi.toml", "pixi.lock"}  # a provisioned environment survives reruns


def reset(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.name not in KEEP:
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()


def prompt(task: dict, staged: list[str]) -> str:
    files = ", ".join(staged)
    if "choices" in task:
        options = "\n".join(f"- {c}" for c in task["choices"])
        return (
            f"{task['question']}\n\nThe table is the image file {files} in the project. Show it "
            "with python_execute (IPython.display.Image) and read it.\n"
            f"Choose exactly one option:\n{options}\n\n"
            "End with one line: FINAL ANSWER: <the option text, copied exactly>"
        )
    return (
        f"{task['question']}\n\nThe input data are in the project: {files}. Analyse them with "
        "python_execute; archives may need to be extracted first. Report the number to two "
        "decimal places.\nEnd with one line: FINAL ANSWER: <number only>"
    )


async def run_task(
    task: dict, args: argparse.Namespace, *, make_client=research.model_client, isolated=False
) -> dict:
    root = args.output.resolve() / task["id"]
    reset(root)
    staged = stage(task, args.data, root, args.pixi_env)
    ws = workspace.init_project(root, classification=Classification.PUBLIC)
    providers = {name: {} for name in {args.worker.split(":")[0], args.reviewer.split(":")[0]}}
    ws.config = {
        "providers": providers,
        "roles": {"worker": args.worker, "reviewer": args.reviewer},
        "models": {args.worker: {"vision": True}},
        "agent": {"vision": True, "budget_tokens": args.budget_tokens},
        "kernel": {"pixi": args.pixi_env is not None and task["benchmark"].startswith("bix")},
    }
    if isolated:
        ws.config["kernel"].update(macos_sandbox=True, network=False)
        if ws.kernel_isolation().value not in {"bwrap", "seatbelt"}:
            raise RuntimeError("Evaluation requires an isolated, offline kernel")
    controls = ResearchControls(ws)
    sid = controls.new_session(task["id"])
    client = make_client(ws, controls.store)
    started = time.monotonic()
    answer, error = None, None
    try:
        turn = await research.execute(
            ws,
            controls.store,
            sid,
            prompt(task, staged),
            client=client,
            permissions=lambda servers: workspace.default_permissions(
                ws, allow=[], servers=servers
            ),
            max_steps=args.max_steps,
        )
        text = turn.answer.text()
        (root / "answer.md").write_text(text + "\n", encoding="utf-8")
        found = FINAL.findall(text)
        answer = found[-1].strip().strip("`*").strip() if found else None
        if answer is None:
            error = "no FINAL ANSWER line"
    except Exception as exc:  # recorded as a failure, never as a wrong answer
        error = redact(f"{type(exc).__name__}: {exc}")[:500]
    events = [e.parsed() for e in controls.store.read(sid)]
    calls = [p for p in events if isinstance(p, ev.ModelCallFinished)]
    runs = [p for p in events if isinstance(p, ev.ToolCallFinished)]
    isolation = sorted(
        {str(p.isolation) for p in events if isinstance(p, ev.RunStarted) and p.isolation}
    )
    return {
        "id": task["id"],
        "benchmark": task["benchmark"],
        "answer": answer,
        "failure": error,
        "seconds": round(time.monotonic() - started, 1),
        "tool_calls": len(runs),
        "kernel_isolation": isolation,
        "model_calls": len(calls),
        "input_tokens": sum(c.input_tokens or 0 for c in calls),
        "output_tokens": sum(c.output_tokens or 0 for c in calls),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--pixi-env", type=Path, help="Provisioned pixi workspace for BixBench")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--budget-tokens", type=int, default=1_500_000)
    parser.add_argument("--task", action="append", default=[])
    args = parser.parse_args()
    tasks = json.loads(args.questions.read_text())["tasks"]
    if args.task:
        tasks = [t for t in tasks if t["id"] in args.task]
    results = []
    for task in tasks:
        results.append(await run_task(task, args))
        print(json.dumps(results[-1]), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "predictions.json").write_text(
        json.dumps({r["id"]: r["answer"] for r in results}, indent=2) + "\n"
    )
    (args.output / "runs.json").write_text(
        json.dumps({"worker": args.worker, "reviewer": args.reviewer, "runs": results}, indent=2)
        + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
