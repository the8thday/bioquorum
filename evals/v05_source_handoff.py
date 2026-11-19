"""Compare a recorded-source calculation with the handoff switch off and on."""

import argparse
import asyncio
import json
from pathlib import Path

from v02_demo import DemoClient

from bioquorum.application import research, workspace
from bioquorum.application.controls import ResearchControls
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification, PermissionClass
from bioquorum.core.hashing import sha256_text
from bioquorum.review.binding import find_bindings
from bioquorum.tools import default_registry
from bioquorum.tools.spec import ToolResult, ToolSpec

SOURCE = '{"archived_batch_sizes": [2, 5]}'


async def compare(output: Path) -> None:
    project = workspace.init_project(output, classification=Classification.PUBLIC)
    controls = ResearchControls(project)

    async def records(_args, _ctx):
        return ToolResult.text(SOURCE)

    def registry():
        tools = default_registry()
        tools.register(
            ToolSpec(
                "fixture_records",
                "Read the frozen case",
                records,
                PermissionClass.READ_FS,
                raw_input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                target=lambda _args, ctx: str(ctx.workdir),
            )
        )
        return tools

    reports = []
    path = f"tool-sources/s1-{sha256_text(SOURCE)}.txt"
    code = f"import json\nfrom pathlib import Path\nbq.value('total', sum(json.loads(Path({path!r}).read_text())['archived_batch_sizes']))"
    for enabled in (False, True):
        project.config = {"agent": {"source_to_kernel": enabled}}
        sid = controls.new_session(f"Source handoff {enabled}")
        scripted = DemoClient(
            controls.store,
            [
                ("fixture_records", {}),
                ("stage_tool_source", {"alias": "s1"}),
                ("python_execute", {"code": code, "input_files": [path]}),
            ],
            "The total is {{total}}.",
            [],
        )
        turn = await research.execute(
            project,
            controls.store,
            sid,
            "Calculate the total from the archived source record.",
            client=scripted,
            registry_factory=registry,
            permissions=lambda servers: workspace.default_permissions(
                project, allow=[], servers=servers
            ),
        )
        evidence = research.answer_evidence(controls.store, sid, turn.answer_event_id)
        declared = evidence.values.get("total")
        events = [e.parsed() for e in controls.store.read(sid)]
        reports.append(
            {
                "enabled": enabled,
                "value": declared.value.value if declared else None,
                "bound": any(b.ok for b in find_bindings(turn.answer.text(), evidence)),
                "inputs": [p.input_paths for p in events if isinstance(p, ev.RunStarted)],
                "session_id": sid,
            }
        )
    assert not reports[0]["bound"] and reports[1]["bound"] and reports[1]["value"] == 7
    (output / "summary.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(reports))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(compare(arguments.output.resolve()))
