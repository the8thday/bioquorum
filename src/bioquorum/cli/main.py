"""``bq`` command line (v0.0: database maintenance only)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bioquorum import __version__
from bioquorum.storage.db import Database, default_db_path
from bioquorum.storage.events import EventStore
from bioquorum.storage.rebuild import rebuild as _rebuild

app = typer.Typer(
    help="BioQuorum: model-neutral, cross-verified, reproducible biomedical research."
)
db_app = typer.Typer(help="Event store maintenance.")
app.add_typer(db_app, name="db")
console = Console()

DbPath = Annotated[
    Path | None, typer.Option("--db", help="SQLite path (default: ~/.bioquorum/bioquorum.db)")
]


def _db(path: Path | None) -> Database:
    return Database.at(path or default_db_path())


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


if __name__ == "__main__":
    app()
