"""Execute the frozen three-arm research task evaluation with one spending ledger."""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx2
from v08_budget import DEVELOPMENT_MODEL, MAX_OUTPUT, MeteredOpenAI, SpendingLog
from v12_scale import Lookup

from bioquorum.application import research, workspace
from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.core.refs import QuoteRef
from bioquorum.models import ModelClient, Router
from bioquorum.models.registry import build_model
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.binding import find_bindings
from bioquorum.review.evidence import Evidence
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

ROOT = Path(__file__).parent / "results/v12"
CASES = Path(__file__).parent / "questions/v12-scale-heldout.json"
MODEL = "openai:" + DEVELOPMENT_MODEL


async def arm(case, mode, directory, ledger):
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "answer.json"
    if output.exists():
        return
    root = directory / "project"
    ws = (
        workspace.load(root)
        if (root / "bioquorum.toml").is_file()
        else workspace.init_project(root, classification=Classification.PUBLIC)
    )
    ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": MODEL, "reviewer": MODEL},
        "agent": {
            "binding": True,
            "subtasks": mode != "direct",
            "specialists": mode == "specialist",
            "subtask_tools": ["archive_lookup"],
        },
    }
    if mode == "specialist":
        profiles = root / ".bioquorum/agents"
        profiles.mkdir(parents=True, exist_ok=True)
        (profiles / "archive-reader.md").write_text(
            "---\nname: archive-reader\ndescription: Verify a record in the local archive.\n"
            f"model: {MODEL}\ntools: [archive_lookup]\npermissions: [read_fs]\n---\n"
            "Retrieve the assigned record. Preserve its exact material or cohort designation in the quoted sentence."
        )
    store = EventStore(ws.database())
    sid = workspace.start_session(ws, store, "Paired archive investigation")
    policy = PermissionPolicy()
    policy.grant(PermissionClass.READ_FS, "archive", PermissionScope.SESSION, session_id=sid)
    records = case["records"]

    async def lookup(args, ctx):
        if args.record not in records:
            return ToolResult.text("Unknown record ID.", is_error=True)
        return ToolResult.text(records[args.record])

    registry = ToolRegistry(
        [
            ToolSpec(
                "archive_lookup",
                "Look up one exact record ID in the local archive.",
                lookup,
                PermissionClass.READ_FS,
                args_model=Lookup,
                target=lambda *_: "archive",
            )
        ]
    )
    meter = MeteredOpenAI(ledger, model=DEVELOPMENT_MODEL)
    query = (
        f"Compare the {case['topic']} recorded in {', '.join(records)}. Look up both IDs independently. "
        "When delegate_tasks is available, submit the two ID lookups together; use archive-reader when offered. "
        "For each ID report its designation and bind the exact archive sentence as a source quotation."
    )
    async with httpx2.AsyncClient(transport=meter, timeout=90) as http:
        client = ModelClient(
            Router(ws.models_config()),
            store,
            factory=lambda route: build_model(route, http_client=http),
            max_output_tokens=MAX_OUTPUT,
        )
        started = time.monotonic()
        turn = await research.execute(
            ws,
            store,
            sid,
            query,
            client=client,
            permissions=lambda _: policy,
            max_steps=6,
            registry_factory=lambda: registry,
        )
        elapsed = time.monotonic() - started
    text = turn.answer.text()
    events = store.read(sid)
    evidence = Evidence.from_events(events)
    bindings = find_bindings(text, evidence)
    quotes = [b.ref.quote for b in bindings if b.ok and isinstance(b.ref, QuoteRef)]
    scores = [
        term in text.lower() and any(term in q.lower() and q in source for q in quotes)
        for term, source in zip(case["terms"], records.values(), strict=True)
    ]
    children = [e.payload for e in events if e.type == "subtask_started"]
    report = {
        "session": sid,
        "model": MODEL,
        "question": query,
        "answer": text,
        "mode": mode,
        "scores": scores,
        "quotes": quotes,
        "children": children,
        "seconds": elapsed,
        "calls": len(store.model_usage_rows([sid, *[p["task_id"] for p in children]])),
        "conservative_usd": meter.reserved_usd,
        "unsettled": meter.unsettled,
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(directory.name, scores, len(children), round(elapsed, 2), meter.reserved_usd, flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation", action="store_true")
    confirmation = parser.parse_args().confirmation
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing")
    ROOT.mkdir(parents=True, exist_ok=True)
    ledger = SpendingLog(ROOT / "spending.sqlite", 5_000_000)
    modes = ["direct", "parallel", "specialist"]
    cases = CASES.with_name("v12-scale-confirmation.json") if confirmation else CASES
    destination = ROOT / "confirmation" if confirmation else ROOT
    for i, case in enumerate(json.loads(cases.read_text())):
        for mode in modes[i:] + modes[:i]:
            await arm(case, mode, destination / f"{i + 1}-{mode}", ledger)


if __name__ == "__main__":
    asyncio.run(main())
