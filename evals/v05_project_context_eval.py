"""Paired local evaluation of progressive skills and cross-session project notes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from v04_judgment_eval import server_request

from bioquorum.application import project_memory, research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification
from bioquorum.runtime import FakeKernel

MODEL = "qwen3:4b-instruct"
# Frozen before the local model run. Context is synthetic; each code is intentionally opaque.
CASES = (
    ("dev", "skills", "calibration", "T7 drift check", "cedar-loop"),
    ("dev", "notes", "relabel", "Pilot Vial L3", "mauve-slip"),
    ("heldout", "skills", "acceptance", "Control W19", "fir-signal"),
    ("heldout", "skills", "routing", "Instrument K2 handoff", "opal-shift"),
    ("heldout", "skills", "triage", "Threshold N7", "plum-node"),
    ("heldout", "notes", "exceptions", "Rack M4", "mint-key"),
    ("heldout", "notes", "convention", "Subset P12", "coral-line"),
    ("heldout", "notes", "unit", "Scanner R8", "teal-band"),
)

# New task names and labels for evaluating the catalog-enum refinement.
NEXT_CASES = (
    ("heldout", "skills", "intake", "Assay F21", "olive-ridge"),
    ("heldout", "skills", "tracking", "Panel Z7", "silver-path"),
    ("heldout", "skills", "dispatch", "Queue Y4", "amber-tile"),
    ("heldout", "notes", "annotation", "Batch K9", "violet-pin"),
    ("heldout", "notes", "review", "Follow-up V2", "pearl-cove"),
    ("heldout", "notes", "registry", "Case S16", "umber-arch"),
)


async def execute(case, enabled: bool, port: int) -> dict:
    split, feature, rule, item, key = case
    with TemporaryDirectory(prefix="bq-context-eval-") as folder:
        ws = workspace.init_project(Path(folder), classification=Classification.PUBLIC)
        ws.config = {
            "providers": {"local": {"kind": "ollama", "base_url": f"http://127.0.0.1:{port}/v1"}},
            "roles": {"worker": "local:" + MODEL},
            "agent": {"skills": False, "project_notes": True},
        }
        if feature == "skills":
            root = ws.state_dir / "skills" / ("workflow-" + rule)
            root.mkdir(parents=True)
            description = json.dumps(f"Project rule for looking up the agreed label for {item}.")
            (root / "SKILL.md").write_text(
                f"---\nname: {root.name}\ndescription: {description}\n---\n"
                f"The researcher-defined label for {item} is {key}. Confirm the exact label from this rule.\n",
                encoding="utf-8",
            )
        controls = ResearchControls(ws)
        if feature == "notes":
            earlier = controls.new_session("Earlier project decisions")
            await project_memory.save(
                controls,
                earlier,
                project_memory.SaveNote(
                    body=f"Agreed label for {item}: {key}. This is a project convention.",
                ),
            )
        ws.config["agent"] = {
            "skills": enabled and feature == "skills",
            "project_notes": enabled and feature == "notes",
        }
        sid = controls.new_session("Fresh research question")

        async def kernel(language):
            return FakeKernel()

        source = "project skill" if feature == "skills" else "saved project note"
        question = (
            f"What is our agreed label for {item}? This is a project convention, not a scientific "
            f"claim. Use the {source} if available. Reply with the exact label or say unavailable."
        )
        started = time.monotonic()
        error = None
        answer = ""
        try:
            result = await asyncio.wait_for(
                research.execute(
                    ws,
                    controls.store,
                    sid,
                    question,
                    client=research.model_client(ws, controls.store),
                    permissions=lambda _: workspace.default_permissions(ws, allow=[], servers=[]),
                    kernel_factory=kernel,
                    max_steps=2,
                ),
                timeout=60,
            )
            answer = result.answer.text()
        except Exception as exc:
            error = type(exc).__name__
        events = [e.parsed() for e in controls.store.read(sid)]
        tools = [
            e.tool for e in events if isinstance(e, ev.ToolCallFinished) and not e.result.is_error
        ]
        relevant = "open_skill" if feature == "skills" else "read_project_note"
        correct = bool(re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", answer, re.I))
        return {
            "split": split,
            "feature": feature,
            "case": rule,
            "enabled": enabled,
            "correct": correct,
            "loaded": relevant in tools,
            "tools": tools,
            "answer": answer,
            "error": error,
            "tokens": sum(
                e.input_tokens + e.output_tokens
                for e in events
                if isinstance(e, ev.ModelCallFinished)
            ),
            "seconds": round(time.monotonic() - started, 2),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--set", choices=("initial", "new"), default="initial")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = {
        **os.environ,
        "OLLAMA_HOST": f"127.0.0.1:{port}",
        "OLLAMA_MODELS": str(args.models_dir),
    }
    with args.output.with_suffix(".server.log").open("wb") as log:
        service = subprocess.Popen(["ollama", "serve"], env=environment, stdout=log, stderr=log)
        try:
            end = time.monotonic() + 15
            while True:
                try:
                    tags = server_request(port, "/api/tags", timeout=1)["models"]
                    if MODEL not in {entry["name"] for entry in tags}:
                        raise ValueError("The configured model is not available locally")
                    break
                except OSError:
                    if time.monotonic() > end or service.poll() is not None:
                        raise
                    time.sleep(0.2)
            rows = []
            for i, case in enumerate(CASES if args.set == "initial" else NEXT_CASES):
                for enabled in (False, True) if i % 2 == 0 else (True, False):
                    row = asyncio.run(execute(case, enabled, port))
                    rows.append(row)
                    args.output.write_text(
                        json.dumps({"model": MODEL, "rows": rows}, indent=2) + "\n"
                    )
                    print(
                        case[:3], enabled, row["correct"], row["loaded"], row["error"], flush=True
                    )
        finally:
            service.terminate()
            try:
                service.wait(timeout=10)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()


if __name__ == "__main__":
    main()
