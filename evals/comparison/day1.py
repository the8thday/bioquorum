"""Print one frozen task or expose its source and kernel tools to the author's general agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import ROOT, load_cases, stage_cells
from v05_target_validation import TARGET
from v05_target_validation import tools as public_tools

from bioquorum.application import workspace
from bioquorum.core.enums import Classification, PermissionClass, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.mcp.server import OutResult, build_server, serve_stdio
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolRegistry, default_registry, invoke_by_name

FROZEN = tuple(f"q-{scenario}-{index}" for scenario in "ABC" for index in (29, 30))


async def serve(case):
    root = ROOT / "comparison" / "generic" / case.id / new_id()
    ws = workspace.init_project(root, classification=Classification.PUBLIC)
    ws.config = {"kernel": {"macos_sandbox": True}}
    if ws.classification is not Classification.PUBLIC or ws.kernel_isolation().value not in {
        "bwrap",
        "seatbelt",
    }:
        raise ValueError("The comparison needs an isolated public workspace")
    with workspace.project_lock(root):
        if case.scenario == "B":
            ws.data_dirs = [stage_cells(root, int(case.id[-2:]))]
        store = EventStore(ws.database())
        sid = workspace.start_session(ws, store, case.id)
        policy = workspace.default_permissions(ws, allow=[], servers=[])
        policy.grant(PermissionClass.CONNECTOR, TARGET, PermissionScope.SESSION, session_id=sid)
        ctx = workspace.tool_context(ws, store, sid, policy)
        async with httpx2.AsyncClient(timeout=15) as http:
            registry = default_registry()
            if case.scenario != "B":
                registry = ToolRegistry([*registry, *public_tools(http)])

            async def call(name, arguments):
                output = await invoke_by_name(registry, name, arguments, ctx)
                return OutResult([output.text()], is_error=output.is_error)

            try:
                await serve_stdio(build_server(registry.definitions(), call, name="day1-sources"))
            finally:
                assert ctx.kernels is not None
                await ctx.kernels.shutdown_all()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=FROZEN)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--rubric", action="store_true")
    args = parser.parse_args()
    case = next(c for c in load_cases() if c.id == args.case)
    if args.serve:
        asyncio.run(serve(case))
    elif args.rubric:
        print(json.dumps(case.criteria, indent=2))
    else:
        if case.scenario == "B":
            print(
                "Use cells.csv, a synthetic paired PBMC panel. G1-G3 are raw UMI counts for three "
                "genes; total_umi includes other genes. mito_fraction is a fraction from 0 to 1."
            )
        print(case.question)
