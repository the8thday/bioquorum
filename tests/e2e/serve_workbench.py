"""Serve the packaged example from an isolated temporary workspace for browser tests."""

import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from bioquorum.application.controls import ResearchControls
from bioquorum.application.demo import make_demo
from bioquorum.application.workspace import init_project, load
from bioquorum.server.app import create_app

with TemporaryDirectory() as directory, socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    host = f"127.0.0.1:{sock.getsockname()[1]}"
    print(f"WORKBENCH_URL=http://{host}/", flush=True)
    real = os.environ.get("BQ_TEST_REAL_PROJECT")
    if os.environ.get("BQ_TEST_PROJECT_NOTE"):
        ws = init_project(Path(directory), name="Notes test project")
        ws.config = {"agent": {"project_notes": True}}
        controls = ResearchControls(ws)
        controls.new_session("Newer research")
        controls.new_session("Older research")
    else:
        controls = ResearchControls(load(Path(real))) if real else make_demo(Path(directory))
    app = create_app(controls, host)
    uvicorn.Server(uvicorn.Config(app, timeout_graceful_shutdown=1)).run(sockets=[sock])
