"""Run scenario A expansion with model-selected queries against public sources."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

import httpx2
import yaml

from bioquorum.application import research, verify, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import (
    Actor,
    Classification,
    MessageRole,
    PermissionClass,
    PermissionScope,
)
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart
from bioquorum.models import ModelClient, ModelUnavailableError, Router
from bioquorum.review.binding import find_bindings, render
from bioquorum.tools import ToolRegistry
from bioquorum.tools.invoke import invoke_by_name
from bioquorum.tools.public_sources import TARGET
from bioquorum.tools.public_sources import public_sources as tools

CASES = Path(__file__).parent / "questions" / "scenario-a.yaml"

REFERENCE_ACTIONS = {
    "q-A-02": [("get_abstract", {"pmid": pmid}) for pmid in ("27807284", "30578352", "39167366")]
    + [("us_drug_label", {"name": "SOTYKTU"}), ("clinical_trials", {"query": "NCT04999839"})],
    "q-A-03": [
        ("get_abstract", {"pmid": pmid})
        for pmid in ("12730697", "16554528", "16909389", "28304224")
    ]
    + [("us_drug_label", {"name": name}) for name in ("REPATHA", "PRALUENT", "LEQVIO")],
    "q-A-04": [
        ("get_abstract", {"pmid": "29562163"}),
        ("baseline_expression", {"symbol": "HSD17B13"}),
        ("clinical_trials", {"query": "NCT05583344"}),
    ],
    "q-A-05": [("get_abstract", {"pmid": pmid}) for pmid in ("16804397", "14570728", "11385576")],
}


def model_settings(args: argparse.Namespace) -> tuple[dict, str, int | None, int]:
    """Provider config, label, max output tokens and review timeout for one evaluation run."""
    if args.worker:
        providers = {name: {} for name in {args.worker.split(":")[0], args.reviewer.split(":")[0]}}
        config = {
            "providers": providers,
            "roles": {"worker": args.worker, "reviewer": args.reviewer},
            "agent": {"binding_retries": 1, "budget_tokens": args.budget_tokens},
        }
        # No output cap: reasoning reviewers can spend thousands of tokens before answering,
        # and the product does not cap remote models either.
        return config, f"{args.worker} / {args.reviewer}", None, 1200
    config = {
        "providers": {"local": {"kind": "ollama", "base_url": args.url}},
        "roles": {"worker": f"local:{args.model}", "reviewer": f"local:{args.model}"},
        "agent": {"binding_retries": 1},
    }
    return config, args.model, 900, 40


async def run_case(
    row: dict, root: Path, settings: tuple[dict, str, int | None, int], reference: bool
) -> dict:
    config, model, max_output, review_timeout = settings
    case_id = row["id"]
    output = root / case_id
    output.mkdir(parents=True, exist_ok=True)
    ws = (
        workspace.load(output)
        if (output / ".bioquorum").exists()
        else workspace.init_project(output, classification=Classification.PUBLIC)
    )
    if ws.classification is not Classification.PUBLIC:
        raise ValueError("Public evaluation requires a public project")
    ws.config = config
    controls = ResearchControls(ws)
    sid = controls.new_session(case_id)
    client = ModelClient(Router(ws.models_config()), controls.store, max_output_tokens=max_output)

    def permissions(servers):
        policy = workspace.default_permissions(ws, allow=[], servers=servers)
        policy.grant(PermissionClass.CONNECTOR, TARGET, PermissionScope.SESSION, session_id=sid)
        return policy

    prompt = row["question"].strip() + (
        " Retrieve and read original evidence before answering. Search for a paper, read its abstract, "
        "and check relevant labels or trial records. Link each factual statement to a short verbatim "
        'passage with [@s1 "exact words"]. Clearly label evidence gaps and inferences.'
    )
    async with httpx2.AsyncClient(timeout=8, headers={"accept": "application/json"}) as http:
        registry = tools(http)
        if reference:
            calls = [
                ToolCallPart(call_id=new_id(), name=name, args=args)
                for name, args in REFERENCE_ACTIONS[case_id]
            ]
            ctx = workspace.tool_context(ws, controls.store, sid, permissions([]))
            asked = controls.store.append(
                ev.UserMessage(
                    message=Message.user(
                        "Collect the preselected evidence records for this evaluation."
                    )
                ),
                session_id=sid,
                actor=Actor.USER,
            )
            selected = controls.store.append(
                ev.AssistantMessage(message=Message(role=MessageRole.ASSISTANT, parts=calls)),
                session_id=sid,
                actor=Actor.SYSTEM,
                causation_id=asked.id,
            )
            capacity = asyncio.Semaphore(3)

            async def collect(call: ToolCallPart) -> None:
                async with capacity:
                    await invoke_by_name(
                        registry,
                        call.name,
                        call.args,
                        ctx,
                        call_id=call.call_id,
                        causation_id=selected.id,
                    )

            try:
                await asyncio.gather(*(collect(call) for call in calls))
            finally:
                assert ctx.kernels is not None
                await ctx.kernels.shutdown_all()
            prompt = row["question"].strip() + (
                " Use the supplied original evidence. Attach an exact short verbatim passage to each "
                'factual statement with [@s1 "exact words"]. Distinguish missing data from evidence of absence.'
            )
        turn = await research.execute(
            ws,
            controls.store,
            sid,
            prompt,
            client=client,
            permissions=permissions,
            registry_factory=ToolRegistry if reference else lambda: registry,
            max_steps=4 if reference else 9,
        )
    evidence = research.answer_evidence(controls.store, sid, turn.answer_event_id)
    bindings = find_bindings(turn.answer.text(), evidence)
    review = None
    audit_error = None
    try:
        async with asyncio.timeout(review_timeout):
            review = await research.audit(
                ws, controls.store, sid, client, answer_event_id=turn.answer_event_id
            )
    except (TimeoutError, ModelUnavailableError) as exc:
        audit_error = f"review_incomplete: {type(exc).__name__}"
    exports = await verify.export_bundle(controls, sid) if evidence.sources else None
    events = [event.parsed() for event in controls.store.read(sid)]
    report = {
        "case": case_id,
        "session": sid,
        "model": model,
        "mode": "preselected reference retrieval" if reference else "model-selected API calls",
        "rubric": row["rubric"],
        "retrieval": [
            {"tool": p.tool, "ok": not p.result.is_error}
            for p in events
            if isinstance(p, ev.ToolCallFinished)
        ],
        "tool_errors": [p.error for p in events if isinstance(p, ev.ToolCallFailed)],
        "sources": [{"alias": s.alias, "tool": s.tool, "text": s.text} for s in evidence.sources],
        "bindings": [{"text": b.raw, "ok": b.ok, "problem": b.problem} for b in bindings],
        "claims": [{"text": p.claim.text, "status": p.status.value} for p in review.claims]
        if review
        else [],
        "review_errors": review.errors if review else [audit_error],
        "findings": [f.evidence for f in review.findings] if review else [],
        "bundle": exports
        and str(verify.bundle_file(controls, sid, exports["id"]).relative_to(output)),
    }
    (output / "answer.md").write_text(turn.answer.text() + "\n", encoding="utf-8")
    (output / "rendered.md").write_text(
        render(turn.answer.text(), evidence) + "\n", encoding="utf-8"
    )
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    counts = Counter(p["status"] for p in report["claims"])
    print(
        case_id,
        "sources",
        len(evidence.sources),
        "bindings",
        sum(b.ok for b in bindings),
        dict(counts),
        flush=True,
    )
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Existing local Ollama /v1 endpoint")
    parser.add_argument("--model", default="qwen3:4b-instruct")
    parser.add_argument("--worker", help="Remote worker model, e.g. openai:gpt-6.1-sol")
    parser.add_argument("--reviewer", help="Remote reviewer model, e.g. deepseek:deepseek-v4-pro")
    parser.add_argument(
        "--budget-tokens", type=int, default=400_000, help="Per-case cap for remote models"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--reference", action="store_true", help="Retrieve fixed source IDs before model synthesis"
    )
    args = parser.parse_args()
    if bool(args.worker) != bool(args.reviewer) or bool(args.worker) == bool(args.url):
        parser.error("Give either --url, or both --worker and --reviewer")
    settings = model_settings(args)
    selected = set(args.case or ("q-A-02", "q-A-03", "q-A-04", "q-A-05"))
    rows = [case for case in yaml.safe_load(CASES.read_text()) if case["id"] in selected]
    if {row["id"] for row in rows} != selected:
        parser.error("Unknown scenario A case")
    if args.reference and not selected <= REFERENCE_ACTIONS.keys():
        parser.error("Reference records are defined for cases 02-05")
    errors = {}
    for row in rows:
        try:
            async with asyncio.timeout(2400 if args.worker else 210):
                await run_case(row, args.output.resolve(), settings, args.reference)
        except Exception as exc:
            errors[row["id"]] = f"{type(exc).__name__}: {exc}"
            print(row["id"], errors[row["id"]], flush=True)
    if errors:
        (args.output / "errors.json").write_text(json.dumps(errors, indent=2) + "\n")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
