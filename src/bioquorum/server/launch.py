"""Run one loopback workbench for a local project or the packaged example."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from bioquorum.application import demo, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.server.app import create_app


@contextmanager
def project_lock(root: Path):
    """Prevent a second workbench from recovering a live one's journal."""
    with (root / workspace.STATE_DIR / "workbench.lock").open("a+b") as handle:
        if not handle.tell():
            handle.write(b"\0")
            handle.flush()
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise workspace.WorkspaceError("A workbench is already open for this project") from None
        yield


def launch(project: Path | None = None, *, port: int = 0, browser: bool = True) -> None:
    root = workspace.find_root(project or Path.cwd())
    if project is not None and root is None:
        raise workspace.WorkspaceError("Initialize the project first with bq project init -C PATH")
    controls = (
        ResearchControls(workspace.load(root))
        if root is not None
        else demo.make_demo(Path.home() / workspace.STATE_DIR / "examples" / "metformin-v03")
    )
    with project_lock(controls.ws.root), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        host = f"127.0.0.1:{sock.getsockname()[1]}"
        url = f"http://{host}/"
        config = uvicorn.Config(
            create_app(controls, host), access_log=False, timeout_graceful_shutdown=2
        )
        server = uvicorn.Server(config)

        def opened() -> None:
            for _ in range(100):
                if server.started:
                    webbrowser.open(url)
                    return
                time.sleep(0.05)

        print(f"BioQuorum workbench · {url}", flush=True)
        if browser:
            threading.Thread(target=opened, daemon=True).start()
        server.run(sockets=[sock])


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the local BioQuorum workbench.")
    parser.add_argument("-C", "--project", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        launch(args.project, port=args.port, browser=not args.no_browser)
    except (workspace.WorkspaceError, OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
