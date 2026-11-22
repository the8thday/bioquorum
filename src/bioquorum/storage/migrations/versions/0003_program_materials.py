"""Research programs, membership and versioned material projections."""

from alembic import op
from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "programs",
        Column("id", String(26), primary_key=True),
        Column("project_id", String(26), nullable=False, index=True),
        Column("name", String(160), nullable=False),
        Column("status", String(16), nullable=False),
        Column("created_at", String(32), nullable=False),
    )
    op.create_table(
        "program_sessions",
        Column("id", String(26), primary_key=True),  # The attached session ID.
        Column("program_id", String(26), nullable=False, index=True),
    )
    op.create_table(
        "material_versions",
        Column("id", String(26), primary_key=True),
        Column("material_id", String(26), nullable=False),
        Column("project_id", String(26), nullable=False),
        Column("program_id", String(26), nullable=False, index=True),
        Column("version", Integer, nullable=False),
        Column("file_id", String(26), nullable=False),
        Column("name", String(180), nullable=False),
        Column("hash", String(64), nullable=False),
        Column("classification", String(16), nullable=False),
        Column("kind", String(32), nullable=False),
        Column("source", Text, nullable=False),
        Column("reason", Text),
        Column("status", String(16), nullable=False),
        Column("withdrawal_reason", Text),
        Column("registered_at", String(32), nullable=False),
        Column("registered_by", String(16), nullable=False),
        UniqueConstraint("material_id", "version"),
        UniqueConstraint("program_id", "file_id"),
    )


def downgrade() -> None:
    op.drop_table("material_versions")
    op.drop_table("program_sessions")
    op.drop_table("programs")
