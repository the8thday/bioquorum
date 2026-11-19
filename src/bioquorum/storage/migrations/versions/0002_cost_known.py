"""Distinguish a zero-cost model from one without configured pricing."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.add_column(
        "model_calls", sa.Column("cost_known", sa.Boolean(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("model_calls", "cost_known")
