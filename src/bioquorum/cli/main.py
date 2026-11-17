"""``bq`` command line."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bioquorum import __version__
from bioquorum.cli import workspace as wsp
from bioquorum.core.enums import Classification, PermissionClass
from bioquorum.mcp.client import McpConnection
from bioquorum.mcp.replay import Cassette, record
from bioquorum.mcp.server import build_server, serve_stdio
from bioquorum.storage.db import Database, default_db_path
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import rebuild as _rebuild
from bioquorum.tools import default_registry
from bioquorum.tools.exits import mcp_call_fn
from bioquorum.tools.external import register_external

app = typer.Typer(
    help="BioQuorum: model-neutral, cross-verified, reproducible biomedical research."
)
db_app = typer.Typer(help="Event store maintenance.")
project_app = typer.Typer(help="Create and inspect a project in the current directory.")
mcp_app = typer.Typer(help="Serve BioQuorum tools over MCP.")
app.add_typer(db_app, name="db")
app.add_typer(project_app, name="project")
app.add_typer(mcp_app, name="mcp")
console = Console()
err = Console(stderr=True)  # stdout belongs to the MCP protocol in `bq mcp serve`

DbPath = Annotated[
    Path | None, typer.Option("--db", help="SQLite path (default: ~/.bioquorum/bioquorum.db)")
]


ProjectDir = Annotated[Path, typer.Option("--project", "-C", help="Project directory")]


def _db(path: Path | None) -> Database:
    """Explicit --db, else the project's database if we are inside one, else the global one."""
    if path is not None:
        return Database.at(path)
    root = wsp.find_root(Path.cwd())
    if root is not None:
        return Database.at(root / wsp.STATE_DIR / "bioquorum.db")
    return Database.at(default_db_path())


def _load(project: Path) -> wsp.Workspace:
    try:
        return wsp.load(project)
    except wsp.WorkspaceError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def version() -> None:
    console.print(f"bioquorum {__version__}")


@db_app.command("init")
def db_init(db: DbPath = None) -> None:
    """Create the database file and schema."""
    database = _db(db)
    console.print(f"initialised {database.url}")


@db_app.command("rebuild")
def db_rebuild(db: DbPath = None) -> None:
    """Drop all projection tables and replay the event log (ADR-002)."""
    n = _rebuild(_db(db))
    console.print(f"replayed {n} events")


@db_app.command("events")
def db_events(
    session_id: Annotated[
        str | None, typer.Argument(help="Session ULID; omit for project-level events")
    ] = None,
    db: DbPath = None,
    limit: int = 50,
) -> None:
    """List events of one session."""
    store = EventStore(_db(db))
    rows = store.read(session_id)[-limit:]
    table = Table("seq", "ts", "type", "actor", "id")
    for e in rows:
        table.add_row(str(e.seq), e.ts.isoformat(timespec="seconds"), e.type, e.actor.value, e.id)
    console.print(table)


# ----------------------------------------------------------------- project


@project_app.command("init")
def project_init(
    project: ProjectDir = Path("."),
    name: Annotated[str | None, typer.Option(help="Defaults to the directory name")] = None,
    classification: Annotated[
        Classification, typer.Option(help="public | internal | confidential")
    ] = Classification.INTERNAL,
) -> None:
    """Make this directory a BioQuorum project."""
    try:
        ws = wsp.init_project(project, name=name, classification=classification)
    except wsp.WorkspaceError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"project [bold]{ws.name}[/bold] ({ws.classification}) at {ws.root}")
    console.print(f"id {ws.project_id}")


@project_app.command("show")
def project_show(project: ProjectDir = Path(".")) -> None:
    ws = _load(project)
    console.print(f"name            {ws.name}")
    console.print(f"id              {ws.project_id}")
    console.print(f"classification  {ws.classification}")
    console.print(f"root            {ws.root}")
    for s in ws.mcp_servers():
        console.print(f"mcp server      {s.name} (clearance: {s.clearance or 'local'})")


# ----------------------------------------------------------------- tools & mcp


@app.command("tools")
def tools_list() -> None:
    """List built-in tools (external MCP tools appear in `bq mcp serve`)."""
    table = Table("name", "permission", "trust", "description")
    for spec in default_registry():
        table.add_row(spec.name, spec.permission.value, spec.output_trust.value, spec.description)
    console.print(table)


@mcp_app.command("serve")
def mcp_serve(
    project: ProjectDir = Path("."),
    allow: Annotated[
        list[PermissionClass] | None,
        typer.Option("--allow", help="Extra permission class to grant (repeatable)"),
    ] = None,
    external: Annotated[
        bool, typer.Option(help="Connect the MCP servers configured in bioquorum.toml")
    ] = True,
) -> None:
    """Serve BioQuorum tools over stdio MCP (for Claude Code, opencode, ...)."""
    ws = _load(project)
    asyncio.run(_serve(ws, allow or [], external))


async def _serve(ws: wsp.Workspace, allow: list[PermissionClass], external: bool) -> None:
    store = EventStore(ws.database())
    session_id = wsp.start_session(ws, store, "mcp serve")
    servers = ws.mcp_servers() if external else []
    registry = default_registry()
    ctx = wsp.tool_context(
        ws, store, session_id, wsp.default_permissions(ws, allow=allow, servers=servers)
    )
    async with AsyncExitStack() as stack:
        for cfg in servers:
            try:
                conn = await stack.enter_async_context(McpConnection(cfg))
                specs = await register_external(registry, conn)
                err.print(f"mcp server {cfg.name}: {len(specs)} tools")
            except Exception as exc:  # one broken server must not take the others down
                err.print(f"[yellow]mcp server {cfg.name} unavailable: {exc}[/yellow]")
        err.print(f"bioquorum MCP server: project {ws.name}, session {session_id}")
        server = build_server(
            registry.definitions(), mcp_call_fn(registry, ctx), version=__version__
        )
        try:
            await serve_stdio(server)
        finally:
            assert ctx.kernels is not None
            await ctx.kernels.shutdown_all()


@mcp_app.command("record")
def mcp_record(
    server: Annotated[str, typer.Argument(help="Server name from bioquorum.toml")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Cassette file (merged if it exists)")],
    call: Annotated[
        list[str] | None,
        typer.Option(
            "--call", help='TOOL=JSON_ARGS, e.g. search_entities={"query_strings":["IL23R"]}'
        ),
    ] = None,
    project: ProjectDir = Path("."),
) -> None:
    """Record a live MCP server's tools and some calls into a replay cassette."""
    ws = _load(project)
    cfgs = {s.name: s for s in ws.mcp_servers()}
    if server not in cfgs:
        err.print(f"[red]no mcp server '{server}' in {wsp.CONFIG_FILE}[/red]")
        raise typer.Exit(1)
    calls: list[tuple[str, dict]] = []
    for spec in call or []:
        tool, sep, raw = spec.partition("=")
        if not sep:
            err.print(f"[red]--call must look like TOOL=JSON, got {spec!r}[/red]")
            raise typer.Exit(2)
        calls.append((tool, json.loads(raw)))
    cfg = dataclasses.replace(cfgs[server], replay=None)  # always record from the live server
    existing = Cassette.load(out) if out.exists() else None

    async def go() -> Cassette:
        async with McpConnection(cfg) as conn:
            return await record(conn, calls, into=existing)

    cassette = asyncio.run(go())
    cassette.save(out)
    console.print(f"{len(cassette.tools)} tools, {len(cassette.calls)} calls -> {out}")


if __name__ == "__main__":
    app()
