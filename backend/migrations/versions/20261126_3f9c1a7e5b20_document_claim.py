"""documents.claim: token of the worker run holding a document, so processing can be stopped or
restarted

Revision ID: 3f9c1a7e5b20
Revises: e27fca8c26a8
Create Date: 2026-11-26 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f9c1a7e5b20"
down_revision: str | Sequence[str] | None = "e27fca8c26a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("claim", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("claim")
