"""Serve a durable, deterministic research project for restart and browser-away acceptance."""

import asyncio
import json
import os
import socket
from pathlib import Path

import uvicorn
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project, load
from bioquorum.core.enums import Classification
from bioquorum.models import ModelClient, Router
from bioquorum.server.app import create_app

root = Path(os.environ["BQ_BACKGROUND_PROJECT"])
restarted = (root / "bioquorum.toml").is_file()
if not restarted:
    init_project(root, classification=Classification.PUBLIC)
    (root / "bioquorum.toml").write_text(
        "[providers.openai]\n[roles]\nworker='openai:fixture'\nreviewer='openai:review'\n"
        "[agent]\nsubtasks=true\nsubtask_tools=['read_file']\n"
    )
ws = load(root)
samples = {"A": "Batch A contains three samples.", "B": "Batch B contains five samples."}
for key, sentence in samples.items():
    (root / f"{key}.txt").write_text(sentence)


async def respond(messages, info):
    parts = [p for m in messages for p in m.parts]
    system = next(p.content for p in parts if isinstance(p, pa.SystemPromptPart))
    if "split a research assistant" in system:
        return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
    query = next(p.content for p in parts if isinstance(p, pa.UserPromptPart))
    returned = [p.tool_name for p in parts if isinstance(p, pa.ToolReturnPart)]
    if isinstance(query, str) and query in samples:
        if not returned:
            return pa.ModelResponse(parts=[pa.ToolCallPart("read_file", {"path": query + ".txt"})])
        return pa.ModelResponse(
            parts=[
                pa.TextPart(
                    content=json.dumps(
                        {
                            "summary": samples[query],
                            "quotes": [{"source": "s1", "quote": samples[query]}],
                        }
                    )
                )
            ]
        )
    if not returned:
        await asyncio.sleep(1)
        return pa.ModelResponse(
            parts=[
                pa.ToolCallPart("delegate_tasks", {"tasks": [{"question": "A"}, {"question": "B"}]})
            ]
        )
    if "python_execute" not in returned:
        return pa.ModelResponse(
            parts=[pa.ToolCallPart("python_execute", {"code": "bq.value('total', sum([3, 5]))"})]
        )
    if not restarted:
        (root / "calculation-recorded").touch()
        await asyncio.Event().wait()
    return pa.ModelResponse(parts=[pa.TextPart(content="Combined sample count: {{total}}.")])


controls = ResearchControls(
    ws,
    make_client=lambda _, store: ModelClient(
        Router(ws.models_config()), store, factory=lambda _: FunctionModel(respond)
    ),
)
if not restarted:
    controls.new_session("Long research")
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    host = f"127.0.0.1:{sock.getsockname()[1]}"
    print(f"WORKBENCH_URL=http://{host}/", flush=True)
    uvicorn.Server(uvicorn.Config(create_app(controls, host), log_level="error")).run(
        sockets=[sock]
    )
