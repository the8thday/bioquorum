"""Paired offline scheduling benchmark with fixed model replies and local source latency."""

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application import research, workspace
from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.models import ModelClient, Router
from bioquorum.policy.permissions import PermissionPolicy
from bioquorum.review.evidence import Evidence
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

DOCUMENTS = {
    "alpha": "The alpha archive records the calibration material as quartz.",
    "beta": "The beta archive records the reference material as graphite.",
}


class Lookup(BaseModel):
    record: str


async def arm(root: Path, enabled: bool):
    ws = workspace.init_project(root, classification=Classification.PUBLIC)
    ws.config = {
        "providers": {"openai": {}},
        "roles": {"worker": "openai:fixture"},
        "agent": {"subtasks": enabled, "subtask_tools": ["archive_lookup"]},
    }
    store = EventStore(ws.database())
    sid = workspace.start_session(ws, store, "Paired archive check")
    activity = 0
    peak = 0

    async def lookup(args, ctx):
        nonlocal activity, peak
        activity += 1
        peak = max(peak, activity)
        try:
            await asyncio.sleep(0.15)
            return ToolResult.text(DOCUMENTS[args.record])
        finally:
            activity -= 1

    def respond(messages, info):
        parts = [p for m in messages for p in m.parts]
        query = next(p.content for p in parts if isinstance(p, pa.UserPromptPart))
        returns = [p for p in parts if isinstance(p, pa.ToolReturnPart)]
        if query in DOCUMENTS:
            if not returns:
                return pa.ModelResponse(
                    parts=[pa.ToolCallPart("archive_lookup", {"record": query})]
                )
            return pa.ModelResponse(
                parts=[
                    pa.TextPart(
                        content=json.dumps(
                            {
                                "summary": "Archive checked.",
                                "quotes": [{"source": "s1", "quote": DOCUMENTS[query]}],
                            }
                        )
                    )
                ]
            )
        if not returns:
            if any(tool.name == "delegate_tasks" for tool in info.function_tools):
                return pa.ModelResponse(
                    parts=[
                        pa.ToolCallPart(
                            "delegate_tasks", {"tasks": [{"question": key} for key in DOCUMENTS]}
                        )
                    ]
                )
            return pa.ModelResponse(
                parts=[pa.ToolCallPart("archive_lookup", {"record": key}) for key in DOCUMENTS]
            )
        return pa.ModelResponse(parts=[pa.TextPart(content="Archive checks complete.")])

    policy = PermissionPolicy()
    policy.grant(PermissionClass.READ_FS, "archive", PermissionScope.SESSION, session_id=sid)
    client = ModelClient(
        Router(ws.models_config()), store, factory=lambda _: FunctionModel(respond)
    )
    registry = ToolRegistry(
        [
            ToolSpec(
                "archive_lookup",
                "Read an archive record",
                lookup,
                PermissionClass.READ_FS,
                args_model=Lookup,
                target=lambda *_: "archive",
            )
        ]
    )
    started = time.monotonic()
    await research.execute(
        ws,
        store,
        sid,
        "Check both archive materials",
        client=client,
        permissions=lambda _: policy,
        registry_factory=lambda: registry,
    )
    elapsed = time.monotonic() - started
    history = store.read(sid)
    evidence = Evidence.from_events(history)
    coverage = sum(any(text in s.text for s in evidence.sources) for text in DOCUMENTS.values())
    ids = [sid, *[e.payload["task_id"] for e in history if e.type == "subtask_started"]]
    calls = store.model_usage_rows(ids)
    return {
        "seconds": elapsed,
        "peak": peak,
        "verified_passages": coverage,
        "model_calls": len(calls),
        "actual_usd": 0,
    }


async def main():
    with tempfile.TemporaryDirectory() as directory:
        for enabled in (False, True):
            rows = [await arm(Path(directory) / f"{enabled}-{i}", enabled) for i in range(3)]
            print(
                json.dumps(
                    {
                        "subtasks": enabled,
                        "median_seconds": statistics.median(r["seconds"] for r in rows),
                        "runs": rows,
                    }
                )
            )
            assert all(
                r["verified_passages"] == 2 and r["peak"] == (2 if enabled else 1) for r in rows
            )


if __name__ == "__main__":
    asyncio.run(main())
