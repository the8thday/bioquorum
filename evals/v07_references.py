"""Pair the same ambiguous follow-up with and without its selected calculation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from bioquorum.agent.context import conversation
from bioquorum.application import workspace
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, ModelRole
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import Message
from bioquorum.core.refs import NamedValue
from bioquorum.models import ModelClient, Router
from bioquorum.storage.events import EventStore

# Each record is a synthetic calculation. Only the reference identifies one of the
# two displayed results. Freeze the split before calling a model.
CASES = (
    (
        "development",
        (("valid_count", "sum", "accepted"), ("typical_duration", "median", "durations")),
        0,
    ),
    ("heldout", (("max_signal", "max", "intensity"), ("fewest_reads", "min", "read_depth")), 1),
    ("heldout", (("total_mass", "sum", "weights"), ("mean_rate", "mean", "rates")), 0),
)
QUESTION = (
    "How was this number calculated? Identify the Python aggregation and its input column. "
    'Reply only as JSON with keys "operation" and "column". Use null when ambiguous.'
)


def fixture(pair, selected):
    payloads = []
    for name, operation, column in pair:
        code = f'bq.value("{name}", table["{column}"].{operation}())'
        digest = sha256_bytes(code.encode())
        payloads.extend(
            [
                ev.RunStarted(run_id=name, kernel_id="fixture", code=code, code_hash=digest),
                ev.RunFinished(
                    run_id=name, run_key=digest, values=[NamedValue(name=name, value=12)]
                ),
            ]
        )
    previous = Message.assistant(
        "\n".join(f"{name}: 12, computed using {op} on {col}." for name, op, col in pair)
    )
    turn = ev.TurnRequested(
        question=QUESTION,
        references=[ev.TurnReference(kind="value", id=pair[selected][0], name=pair[selected][0])],
    )
    events = [
        ev.Event.build(p, session_id="fixture", seq=i, actor=Actor.TOOL)
        for i, p in enumerate(payloads)
    ]

    def append(p, actor):
        e = ev.Event.build(p, session_id="fixture", seq=len(events), actor=actor)
        events.append(e)
        return e.id

    append(ev.AssistantMessage(message=previous), Actor.AGENT)
    turn_id = append(turn, Actor.USER)
    append(ev.TurnStarted(turn_id=turn_id), Actor.SYSTEM)
    append(ev.UserMessage(message=Message.user(QUESTION)), Actor.USER)
    return events


async def evaluate(model: str, output: Path):
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is missing in this process. Run from the terminal where it is exported."
        )
    with TemporaryDirectory() as directory:
        ws = workspace.init_project(Path(directory), classification=Classification.PUBLIC)
        ws.config = {"providers": {"openai": {}}, "roles": {"worker": model}}
        store = EventStore(ws.database())
        client = ModelClient(Router(ws.models_config()), store, max_output_tokens=512)
        rows = []
        for index, (split, pair, selected) in enumerate(CASES):
            events = fixture(pair, selected)
            for enabled in [False, True] if index % 2 == 0 else [True, False]:
                sid = workspace.start_session(ws, store, "Reference evaluation")
                result = await client.complete(
                    ModelRole.WORKER,
                    conversation(events, references=enabled),
                    session_id=sid,
                    classification=ws.classification,
                )
                raw = result.message.text()
                try:
                    value = json.loads(raw)
                except ValueError:
                    value = None
                _, op, col = pair[selected]
                rows.append(
                    {
                        "split": split,
                        "case": index,
                        "references": enabled,
                        "answer": raw,
                        "correct": value == {"operation": op, "column": col},
                        "abstained": value == {"operation": None, "column": None},
                        "tokens": result.usage,
                        "latency_ms": result.latency_ms,
                        "estimated_usd": result.cost_usd,
                    }
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({"model": model, "results": rows}, indent=2) + "\n")
                print(split, index, enabled, rows[-1]["correct"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="openai:gpt-6.1-sol")
    parser.add_argument("--output", type=Path, default=Path("evals/results/v07/references.json"))
    args = parser.parse_args()
    asyncio.run(evaluate(args.model, args.output))
