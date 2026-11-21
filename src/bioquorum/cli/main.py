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
from bioquorum.application import programs
from bioquorum.application import workspace as wsp
from bioquorum.application.controls import ResearchControls
from bioquorum.core.enums import Classification, ModelRole, PermissionClass
from bioquorum.mcp.replay import Cassette, record
from bioquorum.mcp.server import build_server, serve_stdio
from bioquorum.models import ConfigError, ModelUnavailableError, Router
from bioquorum.policy.classification import PolicyViolation
from bioquorum.runtime.sandbox import Isolation, IsolationUnavailable
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
program_app = typer.Typer(help="List and inspect research programs.")
app.add_typer(program_app, name="program")
mcp_app = typer.Typer(help="Serve BioQuorum tools over MCP.")
app.add_typer(db_app, name="db")
app.add_typer(project_app, name="project")
app.add_typer(mcp_app, name="mcp")
console = Console()


@app.command("egress")
def egress_cmd(
    allow: Annotated[list[str], typer.Option("--allow", help="Exact public HTTPS hostname")],
    port: Annotated[int, typer.Option(min=0, max=65535)] = 0,
) -> None:
    """Run the local HTTPS egress proxy."""
    from bioquorum.server.egress import serve_egress

    try:
        asyncio.run(serve_egress(allow, port))
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        pass


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


def _load(project: Path, owner: typer.Context | None = None) -> wsp.Workspace:
    try:
        ws = wsp.load(project)
        if owner is not None:
            owner.with_resource(wsp.project_lock(ws.root))
        return ws
    except wsp.WorkspaceError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _check_isolation(ws: wsp.Workspace) -> None:
    """Say how kernel code will be isolated before anything runs; refuse if it must be and
    cannot (ADR-017)."""
    try:
        mode = ws.kernel_isolation()
    except (IsolationUnavailable, wsp.WorkspaceError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if mode is Isolation.NONE:
        err.print(
            "[yellow]warning: kernel isolation is not available on this machine. Code the "
            "model writes can read your files and reach the network. See SECURITY.md.[/yellow]"
        )
    elif mode is Isolation.BWRAP_NET:
        err.print(
            "[yellow]kernel network access is enabled for this project ([kernel] network = true)[/yellow]"
        )


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


@program_app.command("list")
def program_list(project: ProjectDir = Path(".")) -> None:
    """List all research programs in the selected project."""
    typer.echo(
        json.dumps(
            programs.list_programs(ResearchControls(_load(project))), ensure_ascii=False, indent=2
        )
    )


@program_app.command("show")
def program_show(program_id: str, project: ProjectDir = Path(".")) -> None:
    """Show the program's sessions and the latest status of their claims."""
    try:
        detail = programs.show(ResearchControls(_load(project)), program_id)
    except LookupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(detail, ensure_ascii=False, indent=2))


@app.command("models")
def models_show(project: ProjectDir = Path(".")) -> None:
    """Show which model each role uses under this project's classification."""
    ws = _load(project)
    try:
        router = Router(ws.models_config())
    except ConfigError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    table = Table("role", "model chain (first is used)", "family")
    for role in ModelRole:
        routes = router.chain(role, ws.classification)
        table.add_row(
            role.value,
            " -> ".join(str(r.spec) for r in routes) or "[red]none allowed[/red]",
            routes[0].family if routes else "",
        )
    console.print(f"classification: {ws.classification}")
    console.print(table)


# ----------------------------------------------------------------- run & review


@app.command("run")
def run_cmd(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help="What to investigate")],
    project: ProjectDir = Path("."),
    session: Annotated[str | None, typer.Option(help="Continue this session")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Grant every permission")] = False,
    review: Annotated[bool, typer.Option(help="Review the answer afterwards")] = True,
    max_steps: Annotated[
        int | None,
        typer.Option(min=1, help="Exploration steps (default: 12 when no budget is configured)"),
    ] = None,
    stream: Annotated[bool | None, typer.Option(help="Show live model generation progress")] = None,
    online_citations: Annotated[
        bool,
        typer.Option(
            "--online-citations", help="Resolve retrieved PMIDs and DOIs online (public projects)"
        ),
    ] = False,
) -> None:
    """Ask a question, run tools, and review the resulting answer."""
    from bioquorum.cli.runner import run_question

    ws = _load(project, ctx)
    _check_isolation(ws)
    try:
        code = asyncio.run(
            run_question(
                ws,
                question,
                console=console,
                err=err,
                session_id=session,
                yes=yes,
                review=review,
                max_steps=max_steps,
                stream=stream,
                online_citations=online_citations,
            )
        )
    except (ConfigError, wsp.WorkspaceError, PolicyViolation, ModelUnavailableError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    raise typer.Exit(code)


@app.command("workbench")
def workbench_cmd(
    project: Annotated[Path | None, typer.Option("--project", "-C")] = None,
    port: Annotated[int, typer.Option(min=0, max=65535)] = 0,
    browser: bool = True,
) -> None:
    """Open the local research workbench."""
    from bioquorum.server.launch import launch

    try:
        launch(project, port=port, browser=browser)
    except (wsp.WorkspaceError, OSError, ValueError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@app.command("skills")
def skills_cmd(project: ProjectDir = Path(".")) -> None:
    """List available project skills without opening their bodies."""

    ws = _load(project)
    enabled = ws.agent_options().skills
    console.print_json(
        data={
            "enabled": enabled,
            "skills": {key: item.description for key, item in ws.project_skills().items()},
        }
    )


@app.command("note")
def note_cmd(
    ctx: typer.Context,
    session: str,
    file: Annotated[Path | None, typer.Option(help="Save your edited note from this file.")] = None,
    revision: Annotated[
        str | None, typer.Option(help="Revision returned when you read the note.")
    ] = None,
    project: ProjectDir = Path("."),
) -> None:
    """Show the project note, or save an explicit researcher edit."""
    from bioquorum.application import project_memory
    from bioquorum.application.controls import ResearchControls

    controls = ResearchControls(_load(project, ctx if file is not None else None))
    try:
        if file is None:
            result = project_memory.snapshot(controls, session)
        else:
            edit = project_memory.SaveNote(
                body=file.read_text(encoding="utf-8"), prior_revision=revision
            )
            result = asyncio.run(project_memory.save(controls, session, edit))
    except (LookupError, OSError, ValueError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    console.print_json(data=result)


@app.command("rerun")
def rerun_cmd(
    ctx: typer.Context, session: str, claim: str, project: ProjectDir = Path(".")
) -> None:
    """Compare a reviewed claim's recorded runs with fresh executions."""
    from bioquorum.application.controls import ResearchControls
    from bioquorum.application.verify import rerun

    try:
        result = asyncio.run(rerun(ResearchControls(_load(project, ctx)), session, claim))
        console.print_json(data=result)
    except (ValueError, LookupError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@app.command("replicate")
def replicate_cmd(
    ctx: typer.Context,
    session: str,
    claim: str,
    request: Annotated[
        Path | None, typer.Option(help="Explicit blind request with approved estimate.")
    ] = None,
    project: ProjectDir = Path("."),
) -> None:
    """Show a replication estimate, or execute an explicitly prepared blind request."""
    from bioquorum.application.controls import ResearchControls
    from bioquorum.application.verify import BlindRequest, replicate, replication_quote

    controls = ResearchControls(_load(project, ctx if request is not None else None))
    try:
        if request is None:
            result = replication_quote(controls, session, claim)
        else:
            plan = BlindRequest.model_validate_json(request.read_bytes())
            result = asyncio.run(replicate(controls, session, claim, plan))
        console.print_json(data=result)
    except (ValueError, LookupError, OSError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@app.command("export")
def export_cmd(ctx: typer.Context, session: str, project: ProjectDir = Path(".")) -> None:
    """Export a portable research bundle for the selected session."""
    from bioquorum.application.controls import ResearchControls
    from bioquorum.application.verify import bundle_file, export_bundle

    try:
        controls = ResearchControls(_load(project, ctx))
        result = asyncio.run(export_bundle(controls, session))
        console.print(str(bundle_file(controls, session, result["id"])), markup=False)
    except (ValueError, LookupError) as exc:
        err.print(str(exc), markup=False)
        raise typer.Exit(1) from exc


@app.command("review")
def review_cmd(
    ctx: typer.Context,
    session: Annotated[str | None, typer.Argument(help="Session id (default: the latest)")] = None,
    project: ProjectDir = Path("."),
    answer_file: Annotated[
        Path | None, typer.Option(help="Review this answer text instead of the last one")
    ] = None,
    model_checks: Annotated[
        bool, typer.Option(help="Also run citation_supports with the reviewer model")
    ] = True,
    online_citations: Annotated[
        bool,
        typer.Option(
            "--online-citations", help="Resolve retrieved PMIDs and DOIs online (public projects)"
        ),
    ] = False,
) -> None:
    """Review the last answer of a session claim by claim."""
    from bioquorum.cli.runner import print_review, review_session
    from bioquorum.review import NothingToReviewError

    ws = _load(project, ctx)
    sid = session or _latest_session(ws)
    if sid is None:
        err.print("[red]no sessions in this project[/red]")
        raise typer.Exit(1)
    try:
        result = asyncio.run(
            review_session(
                ws,
                console=console,
                session_id=sid,
                answer_file=answer_file,
                model_checks=model_checks,
                online_citations=online_citations,
            )
        )
    except (NothingToReviewError, ConfigError, PolicyViolation, ModelUnavailableError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[dim]session {sid}[/]")
    print_review(console, result)


def _latest_session(ws: wsp.Workspace) -> str | None:
    from bioquorum.storage.rebuild import snapshot

    rows = sorted(snapshot(ws.database())["sessions"], key=lambda r: r["id"])
    return rows[-1]["id"] if rows else None


@app.command("usage")
def usage_cmd(session: str, project: ProjectDir = Path(".")) -> None:
    """Show recorded turn, session and project usage as JSON."""
    from bioquorum.application.usage import report

    ws = _load(project)
    try:
        typer.echo(json.dumps(report(ws, EventStore(ws.database()), session)))
    except LookupError as exc:
        err.print(str(exc))
        raise typer.Exit(1) from exc


@app.command("sessions")
def sessions_cmd(project: ProjectDir = Path(".")) -> None:
    """List this project's sessions, newest last."""
    from bioquorum.storage.rebuild import snapshot

    ws = _load(project)
    table = Table("id", "created", "title")
    for r in sorted(snapshot(ws.database())["sessions"], key=lambda r: r["id"]):
        table.add_row(r["id"], r["created_at"][:19], r["title"])
    console.print(table)


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
    ctx: typer.Context,
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
    ws = _load(project, ctx)
    _check_isolation(ws)
    asyncio.run(_serve(ws, allow or [], external))


async def _serve(ws: wsp.Workspace, allow: list[PermissionClass], external: bool) -> None:
    store = EventStore(ws.database())
    session_id = wsp.start_session(ws, store, "mcp serve")
    servers = ws.mcp_servers() if external else []
    from bioquorum.application.environment import package_inventory

    inventory = package_inventory(ws.root, use_pixi=bool(ws.config.get("kernel", {}).get("pixi")))
    registry = default_registry(r_available=inventory["r_available"])
    ctx = wsp.tool_context(
        ws, store, session_id, wsp.default_permissions(ws, allow=allow, servers=servers)
    )
    async with AsyncExitStack() as stack:
        for cfg in servers:
            try:
                conn = await stack.enter_async_context(ws.mcp_connection(cfg))
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
        async with ws.mcp_connection(cfg) as conn:
            return await record(conn, calls, into=existing)

    cassette = asyncio.run(go())
    cassette.save(out)
    console.print(f"{len(cassette.tools)} tools, {len(cassette.calls)} calls -> {out}")


if __name__ == "__main__":
    app()
