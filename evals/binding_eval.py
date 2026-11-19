"""Paired ADR-018 binding ablation on frozen public fixtures. Live runs are opt-in."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import tomllib
from collections import Counter
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from bioquorum.agent import run_turn
from bioquorum.application import workspace
from bioquorum.application.research import answer_evidence
from bioquorum.core import events as ev
from bioquorum.core.enums import (
    ClaimKind,
    Classification,
    ModelRole,
    PermissionClass,
    PermissionScope,
)
from bioquorum.core.hashing import sha256_file
from bioquorum.core.refs import NamedValue, ValueRef
from bioquorum.models import ModelClient, Router
from bioquorum.models.config import ModelsConfig, ModelSpec
from bioquorum.policy.secrets import SecretError, redact, resolve_secret
from bioquorum.review import review_answer
from bioquorum.review.binding import find_bindings, ground_claim, render
from bioquorum.review.checks.values import ValueMatch
from bioquorum.review.evidence import ClaimInput, DeclaredValue, Evidence, RunEvidence
from bioquorum.runtime.manager import jupyter_factory
from bioquorum.runtime.sandbox import bwrap_available
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, ToolResult, ToolSpec, default_registry

CASES = Path(__file__).with_name("binding-cases.yaml")
ARMS = {"off": (False, 0), "prompt": (True, 0), "default": (True, 2)}  # Earlier product default.
TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
Price = tuple[float, float, float]  # uncached input, cached input, output per million


class Case(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    split: str = Field(pattern=r"^(development|heldout)$")
    question: str
    source: str | None = None
    data: str | None = None
    rubric: dict[str, str] = Field(min_length=1)


def score_rubric(answer: str, evidence: Evidence, rubric: dict[str, str]) -> dict[str, bool]:
    # A quote can be correct while the answer itself says the opposite. Score the prose and
    # rendered values only. This is a lexical check, with the saved answer available for audit.
    for binding in reversed(find_bindings(answer, evidence)):
        if binding.kind == "quote":
            answer = answer[: binding.start] + " " + answer[binding.end :]
    prose = render(answer, evidence, failed=lambda _binding: " ").replace("*", "")
    return {name: bool(re.search(rule, prose, re.I | re.M)) for name, rule in rubric.items()}


def estimate(usage: dict[str, int], price: Price | None) -> float | None:
    if price is None:
        return None
    uncached, cached, output = price
    cache_hits = min(usage["cache_read_tokens"], usage["input_tokens"])
    return (
        (usage["input_tokens"] - cache_hits) * uncached
        + cache_hits * cached
        + usage["output_tokens"] * output
    ) / 1_000_000


def usage_for(calls: list[ev.ModelCallFinished], role: ModelRole, prices: dict[str, Price]) -> dict:
    selected = [call for call in calls if call.role is role]
    totals = {key: sum(getattr(call, key) for call in selected) for key in TOKENS}
    costs = [
        estimate(
            {key: getattr(call, key) for key in TOKENS}, prices.get(f"{call.provider}:{call.model}")
        )
        for call in selected
    ]
    return {
        **totals,
        "calls": len(selected),
        "estimated_usd": sum(cost for cost in costs if cost is not None)
        if all(cost is not None for cost in costs)
        else None,
    }


async def injections() -> list[dict]:
    """Paired, hand-authored numeric controls. The same observed run backs both renderings."""
    evidence = Evidence(
        runs=[RunEvidence("r1", True, "", "Eligible samples: 37\nRejected samples: 48")],
        values={
            name: DeclaredValue(NamedValue(name=name, value=value), "r1")
            for name, value in [("eligible", 37), ("rejected", 48)]
        },
    )
    return await numeric_probes("{{eligible}} samples were eligible.", evidence)


async def numeric_probes(text: str, evidence: Evidence) -> list[dict]:
    """Change one numeric binding without changing the supporting events."""
    grounding = ground_claim(text, evidence)
    if grounding.state != "grounded" or len(grounding.bindings) != 1:
        return []
    binding = grounding.bindings[0]
    if not isinstance(binding.ref, ValueRef):
        return []
    original = evidence.values[binding.ref.name].value.value
    if not isinstance(original, (int, float)):
        return []
    missing = "eval_missing"
    while missing in evidence.values:
        missing += "_"
    other = next(
        (
            item.value
            for item in evidence.values.values()
            if isinstance(item.value.value, (int, float)) and item.value.value != original
        ),
        None,
    )
    variants = [
        ("clean", binding.exact, binding.raw),
        ("missing", "987651", "{{" + missing + "}}"),
    ]
    if other is not None:
        variants += [
            ("coincident", str(other.value), "{{" + missing + "}}"),
            ("wrong_record", str(other.value), "{{" + other.name + "}}"),
        ]
    results = []
    for name, number, marker in variants:
        before, after = text[: binding.start], text[binding.end :]
        bare, marked = before + number + after, before + marker + after
        claim = ClaimInput(1, bare, ClaimKind.NUMERIC)
        results.append(
            {
                "id": name,
                "expected_error": name != "clean",
                "legacy_flagged": bool(await ValueMatch().run([claim], evidence)),
                "bare_state": ground_claim(bare, evidence).state,
                "marker_state": ground_claim(marked, evidence).state,
            }
        )
    return results


async def recorded_probes(output: Path) -> list[dict]:
    """Score saved model-written claims offline against the original event history."""
    rows = []
    for result in sorted(output.glob("worker-*/*/*/result.json")):
        row = json.loads(result.read_text(encoding="utf-8"))
        if "error" in row:
            continue
        ws = workspace.load(result.parent, global_config=result.parent / workspace.CONFIG_FILE)
        evidence = answer_evidence(
            EventStore(ws.database()), row["session_id"], row["answer_event_id"]
        )
        for claim in dict.fromkeys(item["text"] for item in row["claim_texts"]):
            probes = await numeric_probes(claim, evidence)
            if probes:
                rows.append(
                    {
                        **{k: row[k] for k in ("worker", "case", "split", "arm")},
                        "claim": claim,
                        "probes": probes,
                    }
                )
    return rows


async def run_arm(
    root: Path,
    case: Case,
    router: Router,
    arm: str,
    *,
    prices: dict[str, Price],
    max_steps: int,
) -> dict:
    ws = workspace.init_project(root, name=case.id, classification=Classification.PUBLIC)
    store = EventStore(ws.database())
    sid = workspace.start_session(ws, store, case.id)
    permissions = workspace.default_permissions(ws, allow=[], servers=[])
    client = ModelClient(router, store)
    registry = ToolRegistry()
    if case.source is not None:
        source = case.source

        async def read_fixture(_args, _ctx):
            return ToolResult.text(source)

        registry.register(
            ToolSpec(
                name="read_fixture",
                description="Read the frozen study excerpt associated with the question.",
                handler=read_fixture,
                permission=PermissionClass.READ_FS,
                raw_input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        )
        permissions.grant(
            PermissionClass.READ_FS, "read_fixture", PermissionScope.SESSION, session_id=sid
        )
    factory = None
    if case.data is not None:
        if not bwrap_available():
            raise RuntimeError("Kernel cases require Linux with working bubblewrap.")
        (root / "observations.csv").write_text(case.data, encoding="utf-8")
        (root / workspace.CONFIG_FILE).touch()
        registry.register(default_registry().get("python_execute"))
        factory = jupyter_factory(root, sandbox=ws.kernel_sandbox(), require_isolation=True)
    ctx = workspace.tool_context(ws, store, sid, permissions, kernel_factory=factory)
    binding, retries = ARMS[arm]
    start = time.monotonic()
    try:
        try:
            turn = await run_turn(
                case.question,
                client=client,
                registry=registry,
                ctx=ctx,
                max_steps=max_steps,
                binding=binding,
                binding_retries=retries,
            )
        finally:
            assert ctx.kernels is not None
            await ctx.kernels.shutdown_all()
    except Exception as exc:
        return {"error": redact(str(exc)) or type(exc).__name__, "session_id": sid}
    elapsed = time.monotonic() - start
    answer = turn.answer.text()
    evidence = answer_evidence(store, sid, turn.answer_event_id)
    claims, review_errors = Counter(), []
    claim_texts = []
    if answer.strip():
        try:
            review = await review_answer(
                store,
                client,
                session_id=sid,
                classification=ws.classification,
                answer_event_id=turn.answer_event_id,
                use_model_checks=False,
            )
            claims.update(c.grounding.state for c in review.claims)
            claim_texts = [
                {"text": c.claim.text, "binding_state": c.grounding.state} for c in review.claims
            ]
            review_errors.extend(review.errors)
        except Exception as exc:
            review_errors.append(redact(str(exc)) or type(exc).__name__)
    calls = [
        p for event in store.read(sid) if isinstance(p := event.parsed(), ev.ModelCallFinished)
    ]
    markers = Counter(
        f"{b.kind}_{'ok' if b.ok else 'failed'}" for b in find_bindings(answer, evidence)
    )
    return {
        "session_id": sid,
        "answer_event_id": turn.answer_event_id,
        "worker_models": sorted(set(turn.model_specs)),
        "reviewer_models": sorted(
            {f"{c.provider}:{c.model}" for c in calls if c.role is ModelRole.REVIEWER}
        ),
        "answer": answer,
        "rendered": render(answer, evidence, failed=lambda b: f"[unresolved {b.raw}]"),
        "claim_texts": claim_texts,
        "claims": dict(claims),
        "review_errors": review_errors,
        "markers": dict(markers),
        "retries": turn.binding_retries,
        "tool_calls": turn.tool_calls,
        "successful_runs": sum(run.succeeded for run in evidence.runs),
        "stopped_early": turn.stopped_early,
        "worker_wall_seconds": round(elapsed, 3),
        "rubric": score_rubric(answer, evidence, case.rubric),
        "worker_usage": usage_for(calls, ModelRole.WORKER, prices),
        "reviewer_usage": usage_for(calls, ModelRole.REVIEWER, prices),
    }


def routes(worker: str, reviewer: str | None, configured: dict) -> Router:
    roles = {"worker": worker, "reviewer": reviewer or worker}
    provider_names = {ModelSpec.parse(selected).provider for selected in roles.values()}
    providers = {name: configured.get(name, {}) for name in provider_names}
    config = ModelsConfig.from_dict({"providers": providers, "roles": roles})
    for provider in config.providers.values():
        resolve_secret(provider.api_key)
    router = Router(config)
    for role in (ModelRole.WORKER, ModelRole.REVIEWER):
        router.resolve(role, Classification.PUBLIC)
    return router


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def live(args, parser: argparse.ArgumentParser) -> int:
    config = tomllib.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    prices = {spec: tuple(rates) for spec, rates in config.get("prices", {}).items()}
    if args.price:
        if len(args.worker) != 1:
            parser.error("--price requires a single --worker; use [prices] for multiple models")
        prices[args.worker[0]] = tuple(args.price)
    for rates in prices.values():
        if len(rates) != 3 or any(rate < 0 for rate in rates):
            parser.error(
                "prices must list nonnegative uncached input, cached input and output rates"
            )
    try:
        routers = {
            worker: routes(worker, args.reviewer, config.get("providers", {}))
            for worker in args.worker
        }
    except (ValueError, SecretError) as exc:
        parser.error(redact(str(exc)))
    cases = [Case.model_validate(c) for c in yaml.safe_load(CASES.read_text(encoding="utf-8"))]
    ids = {case.id for case in cases}
    if len(ids) != len(cases) or set(args.case or []) - ids:
        parser.error("case IDs must be unique and --case IDs must exist")
    cases = [
        case
        for case in cases
        if (not args.case or case.id in args.case)
        and (args.mode == "all" or (case.data is not None) == (args.mode == "kernel"))
    ]
    if not cases:
        parser.error("no matching cases")
    if any(case.data is not None for case in cases) and not bwrap_available():
        parser.error("kernel cases require working bubblewrap; use --mode source on this host")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    injected = await injections()
    manifest = {
        "corpus_sha256": sha256_file(CASES),
        "workers": args.worker,
        "reviewer": args.reviewer,
        "cases": [case.id for case in cases],
        "max_steps": args.max_steps,
        "prices": prices,
        "arms": ARMS,
        "injections": injected,
    }
    dump(root / "manifest.json", manifest)
    limiter = asyncio.Semaphore(args.jobs)

    async def pair(worker: str, case: Case, index: int) -> list[dict]:
        async with limiter:
            rows = []
            order = list(ARMS)
            order = order[index % len(order) :] + order[: index % len(order)]
            for arm in order:
                location = root / f"worker-{args.worker.index(worker) + 1}" / case.id / arm
                row = await run_arm(
                    location, case, routers[worker], arm, prices=prices, max_steps=args.max_steps
                )
                row.update(case=case.id, split=case.split, arm=arm, worker=worker, order=order)
                dump(location / "result.json", row)
                rows.append(row)
                status = "failed" if "error" in row else "complete"
                print(f"{worker} {case.id} {arm}: {status}", flush=True)
                if "error" in row:  # Do not charge for the other arms of a failed pair.
                    break
            return rows

    groups = await asyncio.gather(
        *(pair(worker, case, index) for worker in args.worker for index, case in enumerate(cases))
    )
    rows = [row for group in groups for row in group]
    dump(root / "results.json", {**manifest, "runs": rows})
    return int(any("error" in row or row["review_errors"] for row in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow billable model calls")
    parser.add_argument("--worker", action="append", metavar="PROVIDER:MODEL")
    parser.add_argument(
        "--reviewer", metavar="PROVIDER:MODEL", help="fixed claim extractor; defaults to worker"
    )
    parser.add_argument("--config", type=Path, help="optional TOML [providers] and [prices] tables")
    parser.add_argument("--case", action="append", help="limit to these case IDs")
    parser.add_argument("--mode", choices=["source", "kernel", "all"], default="all")
    parser.add_argument("--output", type=Path, help="new output directory for live runs")
    parser.add_argument(
        "--probe-results", type=Path, help="score numeric mutations in a saved live run"
    )
    parser.add_argument("--price", nargs=3, type=float, metavar=("INPUT", "CACHED", "OUTPUT"))
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=1, help="concurrent pairs")
    args = parser.parse_args()
    if args.probe_results is not None:
        if args.live or not (args.probe_results / "manifest.json").is_file():
            parser.error("--probe-results requires a saved run and no --live flag")
        print(json.dumps(asyncio.run(recorded_probes(args.probe_results)), indent=2))
        return 0
    if not args.live:
        print(json.dumps(asyncio.run(injections()), indent=2))
        return 0
    if not args.worker or not args.output or len(set(args.worker)) != len(args.worker):
        parser.error("--live requires distinct --worker values and --output")
    if args.max_steps < 1 or args.jobs < 1:
        parser.error("--max-steps and --jobs must be positive")
    return asyncio.run(live(args, parser))


if __name__ == "__main__":
    raise SystemExit(main())
