"""documents.stage holds a stored message (template plus parameters), which exceeds 64 characters

Revision ID: 249b1c3144ba
Revises: c8e2f4a6b1d9
Create Date: 2026-11-24 12:00:00.000000"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "249b1c3144ba"
down_revision: str | Sequence[str] | None = "c8e2f4a6b1d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Progress messages are written as JSON ({"msg": ..., "params": ...}) and translated when they
    # are read, so the column needs unbounded text.
    with op.batch_alter_table("documents") as batch:
        batch.alter_column(
            "stage", existing_type=sa.String(64), type_=sa.Text(), existing_nullable=True
        )


def downgrade() -> None:
    # The stage only describes a document while it is being processed; clearing it loses nothing
    # that is not rewritten by the next processing step.
    op.execute(sa.text("UPDATE documents SET stage = NULL WHERE length(stage) > 64"))
    with op.batch_alter_table("documents") as batch:
        batch.alter_column(
            "stage", existing_type=sa.Text(), type_=sa.String(64), existing_nullable=True
        )
