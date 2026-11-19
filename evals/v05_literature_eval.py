"""Measure direct versus delegated synthesis on frozen synthetic archive records."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel
from v04_judgment_eval import server_request

from bioquorum.application import research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.review.binding import find_bindings
from bioquorum.runtime import FakeKernel
from bioquorum.tools import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec

MODEL = "qwen3:4b-instruct"
TOOL = "archive__search"
CASES = (
    (
        "dev",
        "Which reference was used to check the survey camera?",
        "The survey camera was checked against a blue ceramic tile.",
        "blue ceramic tile",
    ),
    (
        "heldout",
        "What artifact was used to check image scale?",
        "A ruled glass slide was used as the reference for image scale.",
        "ruled glass slide",
    ),
    (
        "heldout",
        "How did the authors preserve the intermediate material before comparison?",
        "The intermediate material was left in a sealed glass vial until comparison.",
        "sealed glass vial",
    ),
    (
        "heldout",
        "Which material served as the optical blank?",
        "Uncoated quartz served as the optical blank throughout the experiment.",
        "uncoated quartz",
    ),
)

NEW_CASES = (
    (
        "heldout",
        "Which standard checked the thickness measurement?",
        "The thickness measurement was checked against a stepped steel shim.",
        "stepped steel shim",
    ),
    (
        "heldout",
        "How was the distance marker constructed?",
        "The distance marker consisted of alternating amber squares.",
        "alternating amber squares",
    ),
    (
        "heldout",
        "What paper stock was used for the optical reference?",
        "The optical reference was printed on matte ivory card stock.",
        "matte ivory card stock",
    ),
)

EXPLICIT_CASES = (
    (
        "heldout",
        "Which source set the background reference?",
        "A blank pale-green card set the background reference.",
        "pale-green card",
    ),
    (
        "heldout",
        "How were the ruler markings produced?",
        "The ruler markings were engraved with a diamond tip.",
        "diamond tip",
    ),
    (
        "heldout",
        "Which surface held the mounted slide?",
        "The mounted slide sat on a brushed aluminium plate for each measurement.",
        "brushed aluminium plate",
    ),
)


class Search(BaseModel):
    query: str


async def measure(case, enabled: bool, port: int, *, explicit: bool) -> dict:
    split, question, result, expected = case
    with TemporaryDirectory(prefix="bq-lit-eval-") as directory:
        ws = workspace.init_project(Path(directory), classification=Classification.PUBLIC)
        ws.config = {
            "providers": {"local": {"kind": "ollama", "base_url": f"http://127.0.0.1:{port}/v1"}},
            "roles": {"worker": "local:" + MODEL},
            "agent": {"literature_subtasks": enabled, "literature_tools": [TOOL]},
        }
        controls = ResearchControls(ws)
        sid = controls.new_session("Synthetic literature benchmark")

        async def search(args, ctx):
            return ToolResult.text("Synthetic archive excerpt.\n" + result)

        def registry() -> ToolRegistry:
            return ToolRegistry(
                [
                    ToolSpec(
                        TOOL,
                        "Search the literature archive and retrieve an exact passage about the query.",
                        search,
                        PermissionClass.EXTERNAL_MCP,
                        args_model=Search,
                        target=lambda *_: "mcp:fixture",
                    )
                ]
            )

        def permissions(servers):
            policy = workspace.default_permissions(ws, allow=[], servers=servers)
            policy.grant(
                PermissionClass.EXTERNAL_MCP, "mcp:fixture", PermissionScope.SESSION, session_id=sid
            )
            return policy

        async def kernel(language):
            return FakeKernel()

        ask = (
            "Look up the archive record to answer this question. Include a BioQuorum source "
            "marker with a verbatim supporting passage. " + question
        )
        t0 = time.monotonic()
        error = None
        answer = ""
        grounded = False
        clean = False
        try:
            turn = await asyncio.wait_for(
                research.execute(
                    ws,
                    controls.store,
                    sid,
                    "/literature " + ask if enabled and explicit else ask,
                    client=research.model_client(ws, controls.store),
                    permissions=permissions,
                    kernel_factory=kernel,
                    registry_factory=registry,
                    max_steps=3,
                ),
                timeout=65,
            )
            answer = turn.answer.text()
            bindings = find_bindings(
                answer, research.answer_evidence(controls.store, sid, turn.answer_event_id)
            )
            grounded = any(binding.ok for binding in bindings)
            clean = grounded and all(binding.ok for binding in bindings)
        except Exception as exc:
            error = type(exc).__name__
        events = [e.parsed() for e in controls.store.read(sid)]
        reports = [p for p in events if isinstance(p, ev.LiteratureFinished)]
        billed = [p for p in events if isinstance(p, ev.ModelCallFinished | ev.LiteratureFinished)]
        return {
            "split": split,
            "question": question,
            "enabled": enabled,
            "answer": answer,
            "correct": expected in answer.casefold(),
            "grounded": grounded,
            "all_bindings_valid": clean,
            "delegated": bool(reports),
            "reports": [{"outcome": report.outcome, "reason": report.reason} for report in reports],
            "error": error,
            "seconds": round(time.monotonic() - t0, 2),
            "tokens": sum(p.input_tokens + p.output_tokens for p in billed),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--set", choices=("initial", "new", "explicit"), default="initial")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as socket_:
        socket_.bind(("127.0.0.1", 0))
        port = socket_.getsockname()[1]
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_MODELS": str(args.models_dir)}
    with args.output.with_suffix(".server.log").open("wb") as log:
        proc = subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=log)
        try:
            until = time.monotonic() + 15
            while True:
                try:
                    tags = server_request(port, "/api/tags", timeout=1)["models"]
                    if MODEL not in {x["name"] for x in tags}:
                        raise ValueError("Model is not available locally")
                    break
                except OSError:
                    if proc.poll() is not None or time.monotonic() > until:
                        raise
                    time.sleep(0.2)
            rows = []
            cases = {"initial": CASES, "new": NEW_CASES, "explicit": EXPLICIT_CASES}[args.set]
            for index, case in enumerate(cases):
                for enabled in (False, True) if index % 2 == 0 else (True, False):
                    row = asyncio.run(measure(case, enabled, port, explicit=args.set == "explicit"))
                    rows.append(row)
                    args.output.write_text(
                        json.dumps({"model": MODEL, "rows": rows}, indent=2) + "\n"
                    )
                    print(
                        row["split"],
                        enabled,
                        row["correct"],
                        row["all_bindings_valid"],
                        row["delegated"],
                        row["reports"],
                        row["error"],
                        flush=True,
                    )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == "__main__":
    main()
