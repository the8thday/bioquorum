"""Serve the packaged example from an isolated temporary workspace for browser tests."""

import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from pydantic_ai import messages as pa
from pydantic_ai.models.function import FunctionModel

from bioquorum.application import workbench_preferences
from bioquorum.application.controls import ResearchControls
from bioquorum.application.demo import make_demo
from bioquorum.application.workspace import init_project, load
from bioquorum.core.enums import Classification
from bioquorum.models import ModelClient, Router
from bioquorum.server.app import create_app


def conversation_controls(directory: str) -> ResearchControls:
    ws = init_project(Path(directory), name="Conversations", classification=Classification.PUBLIC)
    ws.config = {
        "agent": {"references": True},
        "providers": {
            "openai": {},
            "deepseek": {},
            "other": {"kind": "openai", "allowed_classifications": ["internal"]},
        },
        "roles": {"worker": "openai:fixture", "reviewer": "openai:review"},
        "models": {"deepseek:fixture": {"context_window": 32000}, "other:restricted": {}},
    }
    cfg = ws.models_config()

    def respond(messages, info):
        parts = [p for message in messages for p in message.parts]
        if any(
            "split a research assistant" in p.content
            for p in parts
            if isinstance(p, pa.SystemPromptPart)
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content='{"claims": []}')])
        if any(
            isinstance(p, pa.SystemPromptPart) and "Summarize the older" in p.content for p in parts
        ):
            return pa.ModelResponse(parts=[pa.TextPart(content="Earlier questions recorded.")])
        question = next(p.content for p in reversed(parts) if isinstance(p, pa.UserPromptPart))
        if isinstance(question, str) and question.startswith("First **question**"):
            import time

            time.sleep(1.5)
        if question == "Use the installed table skill":
            if not any(
                isinstance(p, pa.ToolReturnPart) and p.tool_name == "open_skill" for p in parts
            ):
                return pa.ModelResponse(
                    parts=[pa.ToolCallPart("open_skill", {"name": "review-table"})]
                )
            loaded = next(
                p for p in parts if isinstance(p, pa.ToolReturnPart) and p.tool_name == "open_skill"
            )
            assert "Inspect the column names." in str(loaded.content)
            return pa.ModelResponse(
                parts=[pa.TextPart(content="The installed skill says to inspect column names.")]
            )
        if question == "Wait for permission":
            return pa.ModelResponse(
                parts=[
                    pa.ToolCallPart(
                        "download_file", {"url": "https://example.org/test"}, tool_call_id="wait"
                    )
                ]
            )
        return pa.ModelResponse(parts=[pa.TextPart(content="Recorded answer to " + str(question))])

    controls = ResearchControls(
        ws,
        make_client=lambda _, store: ModelClient(
            Router(cfg),
            store,
            factory=lambda _: FunctionModel(respond),
        ),
    )
    controls.new_session("Conversation fixture")
    return controls


with TemporaryDirectory() as directory, socket.socket() as sock:
    workbench_preferences.preferences_path = lambda: Path(directory) / "test-ui.json"
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    host = f"127.0.0.1:{sock.getsockname()[1]}"
    print(f"WORKBENCH_URL=http://{host}/", flush=True)
    real = os.environ.get("BQ_TEST_REAL_PROJECT")
    if os.environ.get("BQ_TEST_PROJECTS"):
        from bioquorum.application import project_library

        project_library.library_root = lambda: Path(directory) / ".bioquorum" / "test-library"
    if os.environ.get("BQ_TEST_FIRST_RUN"):
        from unittest.mock import patch

        from bioquorum.application.workspace import Workspace
        from bioquorum.runtime.sandbox import IsolationUnavailable

        ws = init_project(
            Path(directory), name="Fresh project", classification=Classification.PUBLIC
        )
        ws.config = {
            "mcp": {
                "servers": {
                    "library": {
                        "command": "uninstalled-bioquorum-test-fixture",
                        "clearance": "public",
                    }
                }
            }
        }
        patch.object(
            Workspace,
            "kernel_isolation",
            side_effect=IsolationUnavailable("Isolation unavailable in fixture"),
        ).start()
        controls = ResearchControls(ws)
        controls.new_session("Initial session")
    elif os.environ.get("BQ_TEST_UPLOAD"):
        from upload_fixture import upload_controls

        controls = upload_controls(directory)
    elif os.environ.get("BQ_TEST_REFERENCES"):
        from reference_fixture import reference_controls

        controls = reference_controls(directory)
    elif os.environ.get("BQ_TEST_CONVERSATION") or os.environ.get("BQ_TEST_EXTENSIONS"):
        controls = conversation_controls(directory)
    elif os.environ.get("BQ_TEST_PROJECT_NOTE"):
        ws = init_project(Path(directory), name="Notes test project")
        ws.config = {"agent": {"project_notes": True}}
        controls = ResearchControls(ws)
        controls.new_session("Newer research")
        controls.new_session("Older research")
    else:
        controls = ResearchControls(load(Path(real))) if real else make_demo(Path(directory))
    app = create_app(controls, host)
    uvicorn.Server(uvicorn.Config(app, timeout_graceful_shutdown=1)).run(sockets=[sock])
