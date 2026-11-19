"""Run the production binding feedback loop against frozen synthetic failures."""

from __future__ import annotations

import argparse
import asyncio
import re
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from bioquorum.agent import run_turn
from bioquorum.application import workspace
from bioquorum.cli.runner import answer_evidence
from bioquorum.core import events as ev
from bioquorum.core.enums import (
    Classification,
    MessageRole,
    ModelRole,
    PermissionClass,
    PermissionScope,
)
from bioquorum.core.hashing import sha256_file
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, Part, TextPart, ThinkingPart, ToolCallPart
from bioquorum.core.refs import NamedValue, QuoteRef, ValueRef
from bioquorum.models import ModelClient, Router
from bioquorum.models.client import ModelUnavailableError
from bioquorum.policy.secrets import SecretError, redact
from bioquorum.review.binding import ground_claim
from bioquorum.review.evidence import Evidence
from bioquorum.runtime.fake import FakeKernel, Script
from bioquorum.runtime.values import value_output
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, ToolResult, ToolSpec, default_registry

from .binding_eval import TOKENS, Price, dump, routes, usage_for

CASES = Path(__file__).with_name("binding-repair-cases.yaml")


class Target(BaseModel):
    label: str
    value: str | None = None
    source: int | None = None
    description: str = ""


class Case(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    split: str = Field(pattern=r"^(development|heldout)$")
    question: str
    initial: str
    targets: list[Target] = Field(min_length=1)
    values: list[NamedValue] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    clean: bool = False


def _normal(text: str) -> str:
    return " ".join(text.casefold().strip(" .:;").split())


def score_answer(case: Case, answer: str, evidence: Evidence) -> dict[str, str]:
    outcomes = {}
    for target in case.targets:
        pattern = rf"^\s*(?:(?:\d+[.)]|[-•])\s+)?{re.escape(target.label)}\s*:\s*(.+)$"
        matching = [
            match[1]
            for line in answer.replace("*", "").splitlines()
            if (match := re.fullmatch(pattern, line, re.I))
        ]
        if len(matching) != 1:
            outcomes[target.label] = "omitted" if not matching else "changed"
            continue
        claim = matching[0]
        check = ground_claim(claim, evidence)
        if check.state != "grounded":
            outcomes[target.label] = "unresolved" if check.state == "failed" else "unbound"
            continue
        if len(check.bindings) != 1:
            outcomes[target.label] = "changed"
            continue
        binding = check.bindings[0]
        ref = binding.ref
        prose = _normal(claim[: binding.start] + claim[binding.end :])
        correct = False
        if isinstance(ref, ValueRef) and target.value is not None:
            declared = evidence.values[ref.name].value
            correct = ref.name == target.value and prose in ("", _normal(declared.unit or ""))
            expected_value = next(
                value.value for value in case.values if value.name == target.value
            )
            correct = (
                correct
                and declared.value == expected_value
                and binding.rendered == str(expected_value)
            )
        elif isinstance(ref, QuoteRef) and target.source is not None:
            expected_source = case.sources[target.source - 1]
            correct = (
                any(
                    s.call_id == ref.call_id and s.text == expected_source for s in evidence.sources
                )
                and _normal(target.description) in _normal(ref.quote)
                and prose in ("", _normal(target.description))
            )
        outcomes[target.label] = "repaired" if correct else "changed"
    return outcomes


class SeededClient(ModelClient):
    """Replay the two pre-feedback responses, then use the configured provider."""

    def __init__(self, router: Router, store: EventStore, calls: list[ToolCallPart], initial: str):
        super().__init__(router, store)
        self.calls = calls
        self.initial = initial
        self.seeded = 0

    async def _call(
        self, route, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> tuple[Message, dict[str, int]]:
        if self.seeded == 2:
            async with asyncio.timeout(180):
                return await super()._call(route, messages, tools)
        self.seeded += 1
        parts: list[Part]
        if self.seeded == 1:
            parts = [
                ThinkingPart(provider=route.family, text="Retrieve the supplied observations."),
                *self.calls,
            ]
        else:
            parts = [TextPart(text=self.initial)]
        return Message(role=MessageRole.ASSISTANT, parts=parts), dict.fromkeys(TOKENS, 0)


async def run_arm(
    root: Path, case: Case, router: Router, cap: int, prices: dict[str, Price]
) -> dict:
    ws = workspace.init_project(root, name=case.id, classification=Classification.PUBLIC)
    store = EventStore(ws.database())
    session = workspace.start_session(ws, store, case.id)
    policy = workspace.default_permissions(ws, allow=[], servers=[])
    registry = ToolRegistry()
    calls = []
    if case.sources:

        async def read_fixture(args, _ctx):
            index = args.get("index")
            if type(index) is not int or not 1 <= index <= len(case.sources):
                return ToolResult.text("Unknown frozen source index.", is_error=True)
            return ToolResult.text(case.sources[index - 1])

        registry.register(
            ToolSpec(
                name="read_fixture",
                description="Read a numbered excerpt from the frozen source packet.",
                handler=read_fixture,
                permission=PermissionClass.READ_FS,
                raw_input_schema={
                    "type": "object",
                    "properties": {"index": {"type": "integer"}},
                    "required": ["index"],
                    "additionalProperties": False,
                },
            )
        )
        policy.grant(
            PermissionClass.READ_FS, "read_fixture", PermissionScope.SESSION, session_id=session
        )
        calls.extend(
            ToolCallPart(call_id=new_id(), name="read_fixture", args={"index": i})
            for i in range(1, len(case.sources) + 1)
        )
    kernel = FakeKernel()
    if case.values:
        code = "\n".join(
            f"bq.value({v.name!r}, {v.value!r}, {v.unit!r}, {v.desc!r})" for v in case.values
        )
        outputs = [value_output(v.name, v.value, v.unit, v.desc) for v in case.values]
        kernel.on(lambda submitted: submitted == code, Script(outputs=outputs))
        kernel.on(
            lambda _submitted: True,
            Script.fail("ReplayOnly", "Only the frozen run can be replayed."),
        )
        registry.register(default_registry().get("python_execute"))
        calls.append(ToolCallPart(call_id=new_id(), name="python_execute", args={"code": code}))

    async def factory(_language):
        return kernel

    ctx = workspace.tool_context(ws, store, session, policy, kernel_factory=factory)
    prompt = (
        f"{case.question}\n\nFor the final answer, use these exact line labels: "
        + ", ".join(target.label for target in case.targets)
        + ". Use one line per label in Label: answer format. Omit the summary."
    )
    started = time.monotonic()
    try:
        turn = await run_turn(
            prompt,
            client=SeededClient(router, store, calls, case.initial),
            registry=registry,
            ctx=ctx,
            max_steps=6,
            binding_retries=cap,
        )
    except ModelUnavailableError as exc:
        finished = [
            p for e in store.read(session) if isinstance(p := e.parsed(), ev.ModelCallFinished)
        ]
        return {
            "error": redact(str(exc)),
            "session_id": session,
            "usage": usage_for(finished[2:], ModelRole.WORKER, prices),
            "seconds": round(time.monotonic() - started, 3),
        }
    finally:
        assert ctx.kernels is not None
        await ctx.kernels.shutdown_all()
    events = store.read(session)
    final = turn.answer.text()
    initial_evidence = next(
        answer_evidence(store, session, event.id)
        for event in events
        if isinstance(payload := event.parsed(), ev.AssistantMessage)
        and payload.message.text() == case.initial
    )
    initial = score_answer(case, case.initial, initial_evidence)
    expected = "repaired" if case.clean else "unresolved"
    if expected not in initial.values() or set(initial.values()) - {"repaired", expected}:
        raise ValueError(f"Invalid frozen starting answer in {case.id}: {initial}")
    final_evidence = answer_evidence(store, session, turn.answer_event_id)
    billed = [p for e in events if isinstance(p := e.parsed(), ev.ModelCallFinished)][2:]
    return {
        "session_id": session,
        "initial_targets": initial,
        "answer": final,
        "targets": score_answer(case, final, final_evidence),
        "original_evidence_targets": score_answer(case, final, initial_evidence),
        "retries": turn.binding_retries,
        "usage": usage_for(billed, ModelRole.WORKER, prices),
        "extra_tool_calls": turn.tool_calls - len(calls),
        "stopped_early": turn.stopped_early,
        "seconds": round(time.monotonic() - started, 3),
    }


async def run(args) -> int:
    cases = [Case.model_validate(c) for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))]
    if args.case:
        if missing := set(args.case) - {case.id for case in cases}:
            raise ValueError(f"Unknown repair case IDs: {', '.join(sorted(missing))}")
        cases = [case for case in cases if case.id in args.case]
    config = tomllib.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    prices = {spec: tuple(rates) for spec, rates in config.get("prices", {}).items()}
    routers = {worker: routes(worker, None, config.get("providers", {})) for worker in args.worker}
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "corpus_sha256": sha256_file(CASES),
        "prompt_sha256": sha256_file(
            CASES.parents[1] / "src" / "bioquorum" / "agent" / "prompts.py"
        ),
        "workers": args.worker,
        "cases": [case.id for case in cases],
        "caps": [0, 1, 2],
        "prices": prices,
    }
    dump(args.output / "manifest.json", manifest)
    semaphore = asyncio.Semaphore(args.jobs)

    async def pair(worker, case, index):
        async with semaphore:
            rows = []
            for offset in range(3):
                cap = (offset + index) % 3
                destination = (
                    args.output / f"worker-{args.worker.index(worker) + 1}" / case.id / str(cap)
                )
                row = await run_arm(destination, case, routers[worker], cap, prices)
                row.update(worker=worker, case=case.id, split=case.split, cap=cap, clean=case.clean)
                dump(destination / "result.json", row)
                rows.append(row)
                print(
                    f"{worker} {case.id} cap={cap}: {'error' if 'error' in row else row['targets']}",
                    flush=True,
                )
            return rows

    groups = await asyncio.gather(
        *(pair(worker, case, index) for worker in args.worker for index, case in enumerate(cases))
    )
    rows = [row for group in groups for row in group]
    dump(args.output / "results.json", {**manifest, "runs": rows})
    return int(any("error" in row for row in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--worker", action="append", required=True)
    parser.add_argument("--case", action="append", help="limit to these frozen case IDs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1 or len(set(args.worker)) != len(args.worker):
        parser.error("Specify a positive --jobs and distinct workers.")
    try:
        return asyncio.run(run(args))
    except (ValueError, SecretError) as exc:
        parser.error(redact(str(exc)))


if __name__ == "__main__":
    raise SystemExit(main())
