"""Packaged, offline workbench example. Only the fixed exercise can run here."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel

from bioquorum.application import workspace as wsp
from bioquorum.application.controls import ConflictError, ResearchControls
from bioquorum.application.examples.metformin_calculation import chart, summarize
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification, OutputStream, PermissionClass
from bioquorum.models import ModelCassette, ModelClient, ModelsConfig, Router
from bioquorum.runtime import FakeKernel, Script
from bioquorum.runtime.kernel import KernelOutput
from bioquorum.runtime.values import value_output
from bioquorum.tools import ToolRegistry, default_registry
from bioquorum.tools.spec import ToolResult, ToolSpec

QUESTION = "What does the selected literature show about how metformin lowers glucose?"
STEPS = ["Read the selected evidence excerpts.", "Count the excerpts and summarize their scope."]
INPUT = "metformin-evidence.json"
CONFIG = ModelsConfig.from_dict(
    {
        "providers": {"deepseek": {}},
        "roles": {"worker": "deepseek:offline-example", "reviewer": "deepseek:offline-review"},
    }
)
PRICES = {"deepseek:offline-example": (0.0, 0.0), "deepseek:offline-review": (0.0, 0.0)}
RESOURCES = files("bioquorum.application").joinpath("examples")
CODE = RESOURCES.joinpath("metformin_calculation.py").read_text(encoding="utf-8")


class NoArguments(BaseModel):
    pass


def check_request(events: list[ev.Event], question: str, steps: list[str] | None) -> None:
    if question != QUESTION or (steps is not None and steps != STEPS):
        raise ValueError("This offline example takes the supplied question and example plan")
    if any(isinstance(e.parsed(), ev.TurnStarted) for e in events):
        raise ConflictError("Start a new session to run the offline example again")


def make_demo(root: Path) -> ResearchControls:
    root = root.resolve()
    if not (root / wsp.STATE_DIR / "project.json").exists():
        wsp.init_project(
            root, name="Metformin evidence example", classification=Classification.PUBLIC
        )
    ws = wsp.load(root)
    ws.config = {}  # Never inherit a user's live provider, network or MCP configuration here.
    source = RESOURCES.joinpath("metformin.json").read_bytes()
    path = root / INPUT
    if path.is_symlink() or (path.exists() and path.read_bytes() != source):
        raise ValueError("The offline example input differs from the packaged source")
    if not path.exists():
        path.write_bytes(source)
    rows = json.loads(source)

    def registry() -> ToolRegistry:
        async def read_excerpts(args, ctx):
            return ToolResult.text(
                "\n\n".join(f"PMID: {row['pmid']}\n{row['title']}\n{row['quote']}" for row in rows)
            )

        return ToolRegistry(
            [
                default_registry().get("python_execute"),
                ToolSpec(
                    "read_demo_excerpts",
                    "Read the saved literature excerpts.",
                    read_excerpts,
                    PermissionClass.READ_FS,
                    args_model=NoArguments,
                    target=lambda *_: str(path),
                ),
            ]
        )

    async def kernel(language):
        text = summarize(rows)
        svg = chart(rows)
        return FakeKernel().on(
            lambda code: code == CODE,
            Script(
                outputs=[
                    KernelOutput(stream=OutputStream.STDOUT, text=text + "\n"),
                    value_output("excerpts", len(rows), unit="excerpts"),
                    KernelOutput(stream=OutputStream.DISPLAY, data={"image/svg+xml": svg}),
                ]
            ),
        )

    def make_client(ws_, store):
        # Wheel distributions use real resource paths. No keys or network are needed in replay.
        return ModelClient(
            Router(CONFIG),
            store,
            prices=PRICES,
            cassette=ModelCassette(str(RESOURCES.joinpath("metformin-model.json"))),
        )

    return ResearchControls(
        ws, make_client=make_client, kernel_factory=kernel, registry_factory=registry, demo=True
    )
