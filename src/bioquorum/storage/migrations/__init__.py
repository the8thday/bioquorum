"""Packaged Alembic migrations for BioQuorum databases."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection


def migrate(conn: Connection, *, fresh: bool = False, baseline: bool = False) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent).replace("%", "%%"))
    config.attributes["connection"] = conn
    if fresh:
        command.stamp(config, "head")
        return
    if baseline:
        command.stamp(config, "0001")
    command.upgrade(config, "head")
