"""Compare a kernel-only baseline with the current harness on published data."""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import re
import shutil
import time
import tomllib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from bioquorum.agent import Hooks, run_turn
from bioquorum.agent.budget import Budget
from bioquorum.application import workspace
from bioquorum.cli.runner import answer_evidence
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole, PermissionClass, PermissionScope
from bioquorum.core.hashing import sha256_file
from bioquorum.core.messages import Message
from bioquorum.core.refs import QuoteRef, ValueRef
from bioquorum.models import ModelClient, Router
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.policy.secrets import SecretError, redact
from bioquorum.review import review_answer
from bioquorum.review.binding import find_bindings, ground_claim, render
from bioquorum.review.evidence import Evidence
from bioquorum.runtime.manager import jupyter_factory
from bioquorum.runtime.sandbox import bwrap_available
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, default_registry

from .binding_eval import Price, dump, routes, usage_for

CASES = Path(__file__).with_name("harness-research-cases.yaml")
ARMS = {"bare": (False, 0), "full": (True, 0), "full-retry": (True, 1)}
BARE_SYSTEM = "Answer the research question using the provided Python kernel and local files."
NUMBER = re.compile(r"^[+\-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?")


class Case(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    dataset: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    split: str = Field(pattern=r"^(development|heldout)$")
    question: str
    metrics: dict[str, float] = Field(min_length=1)
    design: dict[str, str] = Field(min_length=1)
    source_url: str
    excerpt: str


class TimedClient(ModelClient):
    async def _call(
        self, route, messages: Sequence, tools: Sequence[dict[str, Any]], on_delta=None
    ):
        async with asyncio.timeout(150):
            return await super()._call(route, messages, tools, on_delta=on_delta)


def registry_for(arm: str) -> ToolRegistry:
    builtins = default_registry()
    names = ("python_execute",) if arm == "bare" else ("python_execute", "read_file")
    return ToolRegistry(builtins.get(name) for name in names)


def _field(answer: str, label: str) -> str:
    pattern = rf"\s*(?:(?:[-*•]|\d+[.)])\s+)?{re.escape(label)}\s*:\s*(.*)"
    matches = [
        match[1]
        for line in answer.replace("**", "").splitlines()
        if (match := re.fullmatch(pattern, line, re.I))
    ]
    return matches[0] if len(matches) == 1 else ""


def grade(case: Case, answer: str, evidence: Evidence) -> dict[str, dict[str, bool]]:
    results = {}
    for label in [*case.metrics, *case.design]:
        field = _field(answer, label)
        grounding = ground_claim(field, evidence)
        prose = field
        for binding in reversed(grounding.bindings):
            if binding.kind == "quote":
                prose = prose[: binding.start] + " " + prose[binding.end :]
        rendered = (
            render(prose, evidence, failed=lambda _: " ").lstrip().replace("\u2212", "-").strip("`")
        )
        if label in case.metrics:
            match = NUMBER.match(rendered)
            correct = bool(
                match
                and math.isclose(
                    float(match[0].replace(",", "")), case.metrics[label], abs_tol=1e-5
                )
            )
            traced = any(isinstance(binding.ref, ValueRef) for binding in grounding.bindings)
        else:
            source_text = " ".join(
                b.ref.quote for b in grounding.bindings if isinstance(b.ref, QuoteRef)
            )
            correct = bool(re.search(case.design[label], rendered.strip(" .") or source_text, re.I))
            traced = any(
                isinstance(binding.ref, QuoteRef)
                and any(
                    s.call_id == binding.ref.call_id and case.excerpt in s.text
                    for s in evidence.sources
                )
                for binding in grounding.bindings
            )
        results[label] = {
            "correct": correct,
            "bound": correct and traced and grounding.state == "grounded",
        }
    return results


def validate_inputs(cases: list[Case], path: Path) -> dict:
    with (path / "reference.tsv").open(encoding="utf-8", newline="") as handle:
        reference = {
            (r["id"], r["metric"]): float(r["value"])
            for r in csv.DictReader(handle, delimiter="\t")
        }
    expected = {
        (case.id, label): target for case in cases for label, target in case.metrics.items()
    }
    if reference.keys() != expected.keys() or not all(
        math.isclose(reference[k], v, abs_tol=1e-9) for k, v in expected.items()
    ):
        raise ValueError("Frozen numeric rubric does not match the independent R reference.")
    return {
        "r_version": (path / "r-version.txt").read_text(encoding="utf-8").strip(),
        "data_sha256": {case.dataset: sha256_file(path / f"{case.dataset}.csv") for case in cases},
        "reference_sha256": sha256_file(path / "reference.tsv"),
        "corpus_sha256": sha256_file(CASES),
        "prompt_sha256": sha256_file(
            CASES.parents[1] / "src" / "bioquorum" / "agent" / "prompts.py"
        ),
        "registry_sha256": sha256_file(
            CASES.parents[1] / "src" / "bioquorum" / "models" / "registry.py"
        ),
    }


def _feedback_metrics(store: EventStore, session: str, case: Case, final: dict) -> dict | None:
    events = store.read(session)
    feedback = next(
        (e for e in events if e.actor is Actor.SYSTEM and e.type == ev.UserMessage.event_type), None
    )
    if feedback is None:
        return None
    assert feedback.causation_id is not None
    reply = store.get(feedback.causation_id)
    assert reply is not None
    initial = reply.parsed()
    assert isinstance(initial, ev.AssistantMessage)
    evidence = answer_evidence(store, session, reply.id)
    before = grade(case, initial.message.text(), evidence)
    return {
        "before": before,
        "after": final,
        "initial_failed_markers": sum(
            not b.ok for b in find_bindings(initial.message.text(), evidence)
        ),
        "added_worker_calls": sum(
            isinstance(p := e.parsed(), ev.ModelCallFinished) and p.role is ModelRole.WORKER
            for e in events
            if e.seq > feedback.seq
        ),
    }


async def run_arm(
    root: Path,
    input_dir: Path,
    case: Case,
    router: Router,
    arm: str,
    prices: dict[str, Price],
    max_steps: int,
    *,
    budget: Budget | None = None,
    compact_at_bytes: int | None = None,
    stream: bool = False,
    seed_history: bool = False,
    skip_review: bool = False,
) -> dict:
    ws = workspace.init_project(root, name=case.id, classification=Classification.PUBLIC)
    assert ws.kernel_isolation().value == "bwrap"
    data, note = ws.root / "observations.csv", ws.root / "study.txt"
    shutil.copyfile(input_dir / f"{case.dataset}.csv", data)
    note.write_text(f"Source: {case.source_url}\n\n{case.excerpt}\n", encoding="utf-8")
    (ws.root / workspace.CONFIG_FILE).touch()
    ws.data_dirs = [data, note]
    store = EventStore(ws.database())
    session = workspace.start_session(ws, store, case.id)
    if seed_history:
        for topic in ("missing data", "reporting units"):
            store.append(
                ev.UserMessage(message=Message.user(f"Discuss {topic}.")),
                session_id=session,
                actor=Actor.USER,
            )
            prior = (
                "Earlier generic discussion. Work from the files for a new analysis. "
                "Report the study design and measurement units with the answer. "
            ) * 35
            store.append(
                ev.AssistantMessage(message=Message.assistant(prior)),
                session_id=session,
                actor=Actor.AGENT,
            )
    permissions = PermissionPolicy()
    permissions.grant(
        PermissionClass.EXECUTE, "python", PermissionScope.SESSION, session_id=session
    )
    if arm != "bare":
        permissions.grant(
            PermissionClass.READ_FS, str(note), PermissionScope.SESSION, session_id=session
        )
    if seed_history:
        permissions.grant(
            PermissionClass.READ_FS, str(data), PermissionScope.SESSION, session_id=session
        )
    factory = jupyter_factory(ws.root, sandbox=ws.kernel_sandbox(), require_isolation=True)
    ctx = workspace.tool_context(ws, store, session, permissions, kernel_factory=factory)
    client = TimedClient(router, store)
    binding, retries = ARMS[arm]
    labels = ", ".join([*case.metrics, *case.design])
    question = (
        f"Analyze observations.csv and consult study.txt. {case.question.strip()}\n\n"
        f"Use one line per result in Label: answer format, with these exact labels: {labels}."
    )
    if seed_history:
        question = (
            "Use python_execute to read and analyze observations.csv; use read_file only for "
            "study.txt. These are project files.\n\n" + question
        )
    turn = None
    error = None
    started = time.monotonic()
    first_delta_s = None

    def delta(_text: str) -> None:
        nonlocal first_delta_s
        if first_delta_s is None:
            first_delta_s = round(time.monotonic() - started, 3)

    try:
        turn = await run_turn(
            question,
            client=client,
            registry=registry_for(arm),
            ctx=ctx,
            system=BARE_SYSTEM if arm == "bare" else None,
            max_steps=max_steps if budget is None else None,
            binding=binding,
            binding_retries=retries,
            budget=budget,
            compact_at_bytes=compact_at_bytes,
            hooks=Hooks(on_model_delta=delta if stream else None),
        )
    except Exception as exc:
        error = redact(f"{type(exc).__name__}: {exc}")
    finally:
        assert ctx.kernels is not None
        await ctx.kernels.shutdown_all()
    worker_seconds = round(time.monotonic() - started, 3)
    evidence = Evidence() if turn is None else answer_evidence(store, session, turn.answer_event_id)
    answer = turn.answer.text() if turn else ""
    grades = grade(case, answer, evidence)
    retry = _feedback_metrics(store, session, case, grades)
    claims, review_errors = Counter(), []
    review_start = time.monotonic()
    if turn and answer.strip() and not skip_review:
        try:
            review = await review_answer(
                store,
                client,
                session_id=session,
                classification=Classification.PUBLIC,
                answer_event_id=turn.answer_event_id,
                use_model_checks=arm != "bare",
            )
            claims.update(c.grounding.state for c in review.claims)
            review_errors.extend(review.errors)
        except Exception as exc:
            review_errors.append(redact(f"{type(exc).__name__}: {exc}"))
    events = [e.parsed() for e in store.read(session)]
    calls = [p for p in events if isinstance(p, ev.ModelCallFinished)]
    return {
        "session_id": session,
        "answer_event_id": turn.answer_event_id if turn else None,
        "error": error,
        "review_errors": review_errors,
        "answer": answer,
        "rendered": render(answer, evidence),
        "rubric": grades,
        "claims": dict(claims),
        "markers": dict(
            Counter(
                f"{b.kind}_{'ok' if b.ok else 'failed'}" for b in find_bindings(answer, evidence)
            )
        ),
        "successful_runs": sum(r.succeeded for r in evidence.runs),
        "worker_seconds": worker_seconds,
        "review_seconds": round(time.monotonic() - review_start, 3),
        "worker_usage": usage_for(calls, ModelRole.WORKER, prices),
        "reviewer_usage": usage_for(calls, ModelRole.REVIEWER, prices),
        "summarizer_usage": usage_for(calls, ModelRole.SUMMARIZER, prices),
        "compactions": sum(isinstance(p, ev.ContextCompacted) for p in events),
        "first_delta_s": first_delta_s,
        "stop_reason": turn.stop_reason if turn else None,
        "retry": retry,
        "stopped_early": turn.stopped_early if turn else False,
        "isolation": dict(Counter(p.isolation for p in events if isinstance(p, ev.RunStarted))),
    }


async def run(args) -> int:
    cases = [Case.model_validate(c) for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))]
    inputs = validate_inputs(cases, args.inputs)
    if not args.live:
        print(inputs)
        return 0
    if not bwrap_available():
        raise ValueError("This experiment requires working Linux bubblewrap.")
    config = tomllib.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    prices = {spec: tuple(rates) for spec, rates in config.get("prices", {}).items()}
    routers = {
        worker: routes(worker, args.reviewer, config.get("providers", {})) for worker in args.worker
    }
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        **inputs,
        "workers": args.worker,
        "reviewer": args.reviewer,
        "arms": ARMS,
        "cases": [c.id for c in cases],
        "prices": prices,
        "max_steps": args.max_steps,
    }
    dump(args.output / "manifest.json", manifest)
    semaphore = asyncio.Semaphore(args.jobs)

    async def pair(worker, case, index):
        async with semaphore:
            rows = []
            arm_names = list(ARMS)
            for offset in range(len(arm_names)):
                arm = arm_names[(offset + index) % len(arm_names)]
                location = args.output / f"worker-{args.worker.index(worker) + 1}" / case.id / arm
                row = await run_arm(
                    location, args.inputs, case, routers[worker], arm, prices, args.max_steps
                )
                row.update(worker=worker, case=case.id, split=case.split, arm=arm)
                dump(location / "result.json", row)
                rows.append(row)
                print(worker, case.id, arm, "failed" if row["error"] else row["rubric"], flush=True)
            return rows

    groups = await asyncio.gather(
        *(pair(worker, case, index) for worker in args.worker for index, case in enumerate(cases))
    )
    rows = [r for group in groups for r in group]
    dump(args.output / "results.json", {**manifest, "runs": rows})
    return int(any(r["error"] or r["review_errors"] for r in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--worker", action="append")
    parser.add_argument("--reviewer")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=12)
    args = parser.parse_args()
    if args.live and (
        not args.worker
        or not args.output
        or len(set(args.worker)) != len(args.worker)
        or args.jobs < 1
        or args.max_steps < 1
    ):
        parser.error(
            "--live requires distinct workers, a new output directory and positive limits."
        )
    try:
        return asyncio.run(run(args))
    except (SecretError, ValueError) as exc:
        parser.error(redact(str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
