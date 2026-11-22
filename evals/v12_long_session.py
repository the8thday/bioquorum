"""Measure the workbench on a reproducible 10,000-event project with no provider or kernel."""

import argparse
import json
import statistics
import time
from pathlib import Path

from bioquorum.application import record, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.core.messages import Message
from bioquorum.storage.rebuild import rebuild


def setup(directory: Path):
    if (directory / ".bioquorum/project.json").is_file():
        return ResearchControls(workspace.load(directory))
    controls = ResearchControls(
        workspace.init_project(directory, classification=Classification.PUBLIC)
    )
    sid = controls.new_session("Ten thousand events")
    for i in range(100):
        turn = controls.store.append(
            ev.TurnRequested(question=f"Inspect recorded batch {i}"),
            session_id=sid,
            actor=Actor.USER,
        )
        controls.store.append(ev.TurnStarted(turn_id=turn.id), session_id=sid, actor=Actor.SYSTEM)
        controls.store.append(
            ev.UserMessage(message=Message.user(f"Inspect recorded batch {i}")),
            session_id=sid,
            actor=Actor.USER,
        )
        # Mix audit-bearing tool failures with events that have no projection handler.
        events = []
        for j in range(47):
            events.extend(
                [
                    ev.PlanStepDone(plan_id=turn.id, step_index=j),
                    ev.ToolCallFailed(
                        call_id=f"fixture-{i}-{j}",
                        tool="fixture_lookup",
                        error="Fixture unavailable",
                    ),
                ]
            )
        if i:
            events.append(ev.ExplorationStopped(reason="step limit"))
        events.extend(
            [
                ev.AssistantMessage(
                    message=Message.assistant("Research completed for this batch.")
                ),
                ev.TurnFinished(turn_id=turn.id, outcome="completed"),
            ]
        )
        controls.store.append_chain(events, session_id=sid, actor=Actor.SYSTEM)
    assert len(controls.store.read(sid)) == 10000
    return controls


def timed(fn):
    times = []
    for _ in range(3):
        started = time.perf_counter()
        fn()
        times.append(1000 * (time.perf_counter() - started))
    return round(statistics.median(times), 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    controls = setup(args.directory)
    sid = controls.sessions()[0]["id"]
    if args.serve:
        import socket

        import uvicorn

        from bioquorum.server.app import create_app

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(32)
            host = f"127.0.0.1:{sock.getsockname()[1]}"
            print(f"http://{host}/", flush=True)
            uvicorn.Server(uvicorn.Config(create_app(controls, host), log_level="error")).run(
                sockets=[sock]
            )
        return
    print(
        json.dumps(
            {
                "snapshot_ms": timed(lambda: record.snapshot(controls, sid)),
                "audit_next_50_ms": timed(
                    lambda: record.audit_log(controls, sid, before=5000, limit=50)
                ),
                "rebuild_ms": timed(lambda: rebuild(controls.store.db)),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
