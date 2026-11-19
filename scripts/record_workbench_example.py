"""Regenerate the offline cassette from the declared scripted answers (no provider access)."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application import demo
from bioquorum.models import CassetteMode, ModelCassette, ModelClient, Router

CLAIMS = [
    ("numeric", "This record covers {{excerpts}} selected evidence excerpts."),
    (
        "citation",
        'A small human study reported reduced glucose production [@PMID:11118008 "through a reduction in gluconeogenesis"].',
    ),
    (
        "citation",
        'Primary human hepatocytes showed AMPK activation associated with [@PMID:21947382 "a decrease in cellular energy status"].',
    ),
    (
        "citation",
        'Delayed-release metformin [@PMID:27216492 "enhances fasting and postprandial GLP-1 and PYY"].',
    ),
    ("causal", "The gut pathway accounts for most of metformin's effect in humans."),
]


def script(messages, info):
    parts = [part for msg in messages for part in msg.parts]
    system = next(p.content for p in parts if isinstance(p, pa.SystemPromptPart))
    if "split a research assistant" in system:
        answer = json.dumps({"claims": [{"kind": k, "text": t} for k, t in CLAIMS]})
    elif "check whether cited sources" in system:
        answer = json.dumps(
            {
                "verdicts": [
                    {"claim_index": index, "supported": True, "reason": "Supported by the excerpt."}
                    for index in [2, 3, 4]
                ]
            }
        )
    else:
        done = [p.tool_name for p in parts if isinstance(p, pa.ToolReturnPart)]
        planned = "record_plan_step" in system
        call = None
        if "read_demo_excerpts" not in done:
            call = pa.ToolCallPart("read_demo_excerpts", {}, tool_call_id="sources")
        elif planned and done.count("record_plan_step") == 0:
            call = pa.ToolCallPart("record_plan_step", {"step": 1}, tool_call_id="step_a")
        elif "python_execute" not in done:
            call = pa.ToolCallPart(
                "python_execute",
                {"code": demo.CODE, "input_files": [demo.INPUT]},
                tool_call_id="count",
            )
        elif planned and done.count("record_plan_step") == 1:
            call = pa.ToolCallPart("record_plan_step", {"step": 2}, tool_call_id="step_b")
        if call:
            return pa.ModelResponse(parts=[call])
        answer = "\n\n".join(t for _, t in CLAIMS)
    return pa.ModelResponse(parts=[pa.TextPart(content=answer)])


async def main():
    with TemporaryDirectory() as project, TemporaryDirectory() as out:
        controls = demo.make_demo(Path(project))
        cassette_path = Path(out) / "cassette.json"
        cassette = ModelCassette(cassette_path, CassetteMode.RECORD)
        controls.make_client = lambda _, store: ModelClient(
            Router(demo.CONFIG),
            store,
            cassette=cassette,
            factory=lambda _: FunctionModel(script),
        )
        for steps in (None, demo.STEPS):
            sid = controls.new_session("Metformin example")
            event = controls.submit(sid, demo.QUESTION, steps=steps)
            if steps:
                await controls.decide_plan(sid, event.id, approve=True)
            await controls.tasks[sid].task
            events = controls.require_session(sid)
            assert events[-1].payload["outcome"] == "completed", events[-1].payload
            assert not [e for e in events if e.type in ("error", "model_call_failed")]
        Path(str(demo.RESOURCES.joinpath("metformin-model.json"))).write_bytes(
            cassette_path.read_bytes()
        )


if __name__ == "__main__":
    asyncio.run(main())
