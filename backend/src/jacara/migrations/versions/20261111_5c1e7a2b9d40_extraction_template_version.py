"""extraction template version

Revision ID: 5c1e7a2b9d40
Revises: 8336fb69d646
Create Date: 2026-11-11 16:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e7a2b9d40"
down_revision: str | Sequence[str] | None = "8336fb69d646"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing extractions were produced with the version 1 field list; record them as version 1.
    op.add_column(
        "extractions",
        sa.Column("template_version", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("extractions", "template_version")
