"""Chart reasoning through BioQuorum with a real remote vision model (v0.5 acceptance).

A deterministic chart (evals/assets/vision_chart.R) is shown by a sandboxed Jupyter kernel,
so the image reaches the model the same way a research figure does: as a kernel output
carried by a ``python_execute`` result. The same question is asked with ``[agent] vision``
off and on. Values are not printed on the chart, so correct answers need the image.

    uv run python evals/v05_remote_vision_eval.py --model openai:gpt-6.1-sol \\
        --output evals/results/v05/remote-vision.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path

from bioquorum.application import research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification, MessageRole, ModelRole
from bioquorum.core.ids import new_id
from bioquorum.core.messages import Message, ToolCallPart
from bioquorum.tools import default_registry, invoke

CHART = Path(__file__).parent / "assets" / "vision_chart.png"
TRUTH = {"lowest_sample": "D2-ctrl", "lowest_value": 62, "below": ["D2-ctrl", "D3-ctrl"]}
QUESTION = (
    "Look at the figure produced by the kernel. Answer only with one JSON object with keys "
    '"lowest_sample" (bar label), "lowest_value" (its approximate percentage, a number), '
    '"below" (list of bar labels whose bar is below the dashed threshold line). '
    'If you cannot see the figure, answer {"lowest_sample": null, "lowest_value": null, '
    '"below": null}.'
)


def score(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    try:
        answer = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        answer = None
    if not isinstance(answer, dict):
        return {"parsed": False, "lowest": False, "value": False, "below": False}
    value = answer.get("lowest_value")
    below = answer.get("below")
    return {
        "parsed": True,
        "declined": answer.get("lowest_sample") is None,
        "lowest": answer.get("lowest_sample") == TRUTH["lowest_sample"],
        "value": isinstance(value, int | float) and abs(value - TRUTH["lowest_value"]) <= 3,
        "below": isinstance(below, list) and sorted(map(str, below)) == TRUTH["below"],
    }


async def run(model: str, repeats: int, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="bq-remote-vision-") as directory:
        root = Path(directory)
        (root / "chart.png").write_bytes(CHART.read_bytes())
        ws = workspace.init_project(root, classification=Classification.PUBLIC)
        controls = ResearchControls(ws)
        sid = controls.new_session("remote vision chart reasoning")
        ctx = workspace.tool_context(
            ws, controls.store, sid, workspace.default_permissions(ws, allow=[], servers=[])
        )
        call = ToolCallPart(
            name="python_execute",
            call_id=new_id(),
            args={
                "code": "from IPython.display import Image, display\n"
                "display(Image(filename='chart.png'))",
                "input_files": ["chart.png"],
            },
        )
        try:
            fig = await invoke(
                default_registry().get(call.name), call.args, ctx, call_id=call.call_id
            )
        finally:
            assert ctx.kernels is not None
            await ctx.kernels.shutdown_all()
        if fig.is_error:
            raise RuntimeError(fig.text())
        messages = [
            Message.user(QUESTION),
            Message(role=MessageRole.ASSISTANT, parts=[call]),
            Message.tool(fig),
        ]
        provider = model.split(":", 1)[0]
        runs = []
        for enabled in (False, True):
            ws.config = {
                "providers": {provider: {}},
                "roles": {"worker": model},
                "models": {model: {"vision": True}},
                "agent": {"vision": enabled},
            }
            client = research.model_client(ws, controls.store)
            for attempt in range(repeats):
                reply = await client.complete(
                    ModelRole.WORKER, messages, session_id=sid, classification=ws.classification
                )
                text = reply.message.text()
                runs.append({"vision": enabled, "attempt": attempt, "answer": text, **score(text)})
                print(enabled, attempt, runs[-1], flush=True)
        calls = [e.parsed() for e in controls.store.read(sid) if e.type == "model_call_finished"]
    report = {
        "model": model,
        "api": "default for provider (OpenAI: Responses)",
        "chart": "evals/assets/vision_chart.png (rendered by evals/assets/vision_chart.R)",
        "truth": TRUTH,
        "tolerance": "lowest_value within 3 percentage points",
        "runs": runs,
        "usage": [
            {"input_tokens": c.input_tokens, "output_tokens": c.output_tokens} for c in calls
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="openai:gpt-6.1-sol")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.model, args.repeats, args.output))
