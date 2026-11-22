"""Frozen paired cross-session retrieval evaluation with a cumulative $3 transport cap."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx2
from v08_budget import DEVELOPMENT_MODEL, MAX_OUTPUT, MeteredOpenAI, SpendingLog

from bioquorum.application import program_record, programs, research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, ClaimKind, Classification
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message
from bioquorum.models import ModelClient, Router
from bioquorum.models.registry import build_model
from bioquorum.tools import ToolRegistry

ROOT = Path(__file__).resolve().parent / "results/v10/context"
MODEL = "openai:" + DEVELOPMENT_MODEL
# Cases, expected answers, split, order and scoring fixed before any provider call.
CASES = (
    ("development", "finding", "reader intake", "violet-maple"),
    ("development", "decision", "instrument transfer", "silver-reed"),
    ("heldout", "decision", "second tissue review", "umber-wave"),
    ("heldout", "finding", "archive follow-up", "coral-finch"),
    ("heldout", "revised decision", "release review", "amber-flint"),
    ("heldout", "missing", "shipment handoff", "UNKNOWN"),
)


async def run_case(index, enabled, http, directory):
    split, kind, item, expected = CASES[index]
    ws = workspace.init_project(directory / new_id(), classification=Classification.PUBLIC)
    ws.config = {
        "roles": {"worker": MODEL},
        "providers": {"openai": {}},
        "models": {MODEL: {"input_usd_per_million": 2, "output_usd_per_million": 10}},
        "agent": {"program_context": enabled},
    }
    controls = ResearchControls(ws)
    pid = (await programs.create(controls, "Synthetic project records"))["id"]
    previous = await programs.new_session(controls, pid, "Previous visit")
    label = expected if kind == "finding" else "opal-lake"
    text = f"The recorded finding label for {item} was {label}."
    answer = controls.store.append(
        ev.AssistantMessage(message=Message.assistant(text)), session_id=previous, actor=Actor.AGENT
    )
    cid = new_id()
    controls.store.append(
        ev.ClaimExtracted(claim_id=cid, source_event_id=answer.id, text=text, kind=ClaimKind.PLAN),
        session_id=previous,
        actor=Actor.REVIEWER,
    )
    if kind in ("decision", "revised decision"):
        initial = "cedar-path" if kind == "revised decision" else expected
        saved = await program_record.save_decision(
            controls, pid, f"Use {initial} for {item}.", [cid]
        )
        if kind == "revised decision":
            await program_record.save_decision(
                controls, pid, f"Use {expected} for {item}.", [cid], saved["id"], saved["revision"]
            )
    sid = await programs.new_session(controls, pid, "Fresh session")
    target = "finding label" if kind == "finding" else "currently approved decision code"
    question = (
        f"What was the {target} for {item} from the prior session of this research line? "
        "Return exactly the code. If it is unavailable, return UNKNOWN. These are internal "
        "workflow labels. A finding by itself does not establish an approved decision."
    )
    client = ModelClient(
        Router(ws.models_config()),
        controls.store,
        max_output_tokens=MAX_OUTPUT,
        factory=lambda route: build_model(route, http_client=http),
    )
    start = time.monotonic()
    result = await research.execute(
        ws,
        controls.store,
        sid,
        question,
        client=client,
        permissions=lambda servers: workspace.default_permissions(ws, allow=[], servers=servers),
        registry_factory=ToolRegistry,
        max_steps=3,
    )
    calls = [e.parsed() for e in controls.store.read(sid)]
    usage = [c for c in calls if isinstance(c, ev.ModelCallFinished)]
    answer_text = result.answer.text().strip()
    return dict(
        case=index + 1,
        split=split,
        enabled=enabled,
        correct=answer_text.casefold() == expected.casefold(),
        answer=answer_text,
        input_tokens=sum(c.input_tokens for c in usage),
        output_tokens=sum(c.output_tokens for c in usage),
        calls=len(usage),
        tools=[
            c.tool for c in calls if isinstance(c, ev.ToolCallFinished) and not c.result.is_error
        ],
        seconds=round(time.monotonic() - start, 2),
        session_id=sid,
    )


async def main(split):
    ROOT.mkdir(parents=True, exist_ok=True)
    ledger = SpendingLog(ROOT / "spending.sqlite", 3_000_000)
    transport = MeteredOpenAI(ledger, model=DEVELOPMENT_MODEL)
    async with httpx2.AsyncClient(transport=transport, timeout=90) as http:
        for index, case in enumerate(CASES):
            if split not in ("all", case[0]):
                continue
            for enabled in (False, True) if index % 2 == 0 else (True, False):
                path = ROOT / f"{index + 1}-{'on' if enabled else 'off'}.json"
                if path.exists():
                    continue
                row = await run_case(index, enabled, http, ROOT)
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(row, indent=2) + "\n")
                temp.replace(path)
                print(
                    index + 1,
                    enabled,
                    row["correct"],
                    row["input_tokens"],
                    row["output_tokens"],
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "heldout", "all"], default="all")
    asyncio.run(main(parser.parse_args().split))
