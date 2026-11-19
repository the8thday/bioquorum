"""SQLite database, schema and connection handling."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    select,
)
from sqlalchemy.engine import Engine

from bioquorum.storage.migrations import migrate

SCHEMA_VERSION = 2

metadata = MetaData()

# ----------------------------------------------------------------- source of truth

events = Table(
    "events",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("session_id", String(26), nullable=True, index=True),
    Column("seq", Integer, nullable=False),
    Column("ts", String(32), nullable=False, index=True),
    Column("type", String(64), nullable=False, index=True),
    Column("schema_version", Integer, nullable=False),
    Column("actor", String(16), nullable=False),
    Column("causation_id", String(26), nullable=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
)

schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String(32), primary_key=True),
    Column("value", String(128), nullable=False),
)

# ----------------------------------------------------------------- projections

projects = Table(
    "projects",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("root_dir", Text, nullable=False),
    Column("data_dirs", JSON, nullable=False),
    Column("classification", String(16), nullable=False),
    Column("model_policy", JSON, nullable=False),
    Column("env_id", String(64), nullable=True),
    Column("created_at", String(32), nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("project_id", String(26), nullable=False, index=True),
    Column("title", String(512), nullable=False),
    Column("parent_session_id", String(26), nullable=True),
    Column("forked_at_seq", Integer, nullable=True),
    Column("status", String(16), nullable=False),
    Column("created_at", String(32), nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("session_id", String(26), nullable=False, index=True),
    Column("kernel_id", String(64), nullable=False),
    Column("language", String(16), nullable=False),
    Column("code", Text, nullable=False),
    Column("code_hash", String(64), nullable=False),
    Column("env_lock_hash", String(64), nullable=True),
    Column("inputs", JSON, nullable=False),
    Column("outputs", JSON, nullable=False),
    Column("stdout_ref", JSON, nullable=True),
    Column("run_key", String(64), nullable=True, index=True),
    Column("status", String(16), nullable=False),
    Column("started_at", String(32), nullable=False),
    Column("ended_at", String(32), nullable=True),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("project_id", String(26), nullable=False, index=True),
    Column("name", String(256), nullable=False),
    Column("kind", String(16), nullable=False),
)

artifact_versions = Table(
    "artifact_versions",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("artifact_id", String(26), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("parent_version_id", String(26), nullable=True),
    Column("content_hash", String(64), nullable=False),
    Column("run_id", String(26), nullable=True),
    Column("event_seq_from", Integer, nullable=False),
    Column("event_seq_to", Integer, nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", String(32), nullable=False),
)

claims = Table(
    "claims",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("session_id", String(26), nullable=False, index=True),
    Column("source_event_id", String(26), nullable=False),
    Column("text", Text, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("refs", JSON, nullable=False),
    Column("status", String(16), nullable=False),
)

review_findings = Table(
    "review_findings",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("session_id", String(26), nullable=False, index=True),
    Column("review_id", String(26), nullable=False),
    Column("claim_id", String(26), nullable=True),
    Column("artifact_version_id", String(26), nullable=True),
    Column("check", String(64), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("evidence", Text, nullable=False),
    Column("reviewer_model", String(128), nullable=False),
    Column("status", String(16), nullable=False),
)

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("event_id", String(26), nullable=False),
    Column("session_id", String(26), nullable=False, index=True),
    Column("provider", String(64), nullable=False),
    Column("model", String(128), nullable=False),
    Column("role", String(16), nullable=False),
    Column("classification", String(16), nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_write_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=False),
    Column("cost_known", Boolean, nullable=False, server_default="0"),
    Column("latency_ms", Integer, nullable=False),
)

audit_entries = Table(
    "audit_entries",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("ts", String(32), nullable=False),
    Column("actor", String(16), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", Text, nullable=False),
    Column("classification", String(16), nullable=True),
    Column("detail", JSON, nullable=False),
    Column("policy_violation", Boolean, nullable=False),
)

PROJECTION_TABLES: tuple[Table, ...] = (
    projects,
    sessions,
    runs,
    artifacts,
    artifact_versions,
    claims,
    review_findings,
    model_calls,
    audit_entries,
)


def default_db_path() -> Path:
    home = Path(os.environ.get("BIOQUORUM_HOME", Path.home() / ".bioquorum"))
    return home / "bioquorum.db"


class Database:
    """Owns the engine. Use ``Database.memory()`` in tests."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: Engine = create_engine(url, future=True)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.create_all()

    @classmethod
    def at(cls, path: str | Path) -> Database:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{path}")

    @classmethod
    def memory(cls) -> Database:
        # StaticPool keeps one connection so an in-memory DB survives across sessions.
        from sqlalchemy.pool import StaticPool

        db = cls.__new__(cls)
        db.url = "sqlite://"
        db.engine = create_engine(
            "sqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db.create_all()
        return db

    def create_all(self) -> None:
        with self.engine.begin() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            inspection = inspect(conn)
            if not inspection.has_table(schema_meta.name):
                if inspection.has_table(events.name):
                    raise RuntimeError("BioQuorum database lacks its schema marker")
                metadata.create_all(conn)
                conn.execute(schema_meta.insert().values(key="version", value=str(SCHEMA_VERSION)))
                migrate(conn, fresh=True)
                return
            query = select(schema_meta.c.value).where(schema_meta.c.key == "version")
            version = conn.execute(query).scalar_one_or_none()
            if version not in ("1", str(SCHEMA_VERSION)):
                raise RuntimeError(f"Unsupported BioQuorum schema version: {version}")
            migrate(conn, baseline=version == "1")
            if version != str(SCHEMA_VERSION):
                conn.execute(
                    schema_meta.update()
                    .where(schema_meta.c.key == "version")
                    .values(value=str(SCHEMA_VERSION))
                )

    def begin(self):
        return self.engine.begin()

    def connect(self):
        return self.engine.connect()


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
