"""review tools: pick from source, add and delete table rows

Revision ID: 7a3d9e1f4b62
Revises: 5c1e7a2b9d40
Create Date: 2026-11-11 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a3d9e1f4b62"
down_revision: str | Sequence[str] | None = "5c1e7a2b9d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # Review tools: new evidence when selecting from source text, adding and removing table rows,
    # and stored OCR results for selection.
    op.add_column("value_revisions", sa.Column("evidence", _JSON, nullable=True))
    op.add_column(
        "extracted_values",
        sa.Column("origin", sa.String(length=16), server_default="model", nullable=False),
    )
    op.add_column(
        "extracted_values", sa.Column("sort_key", sa.Float(), server_default="0", nullable=False)
    )
    # Existing values: display order equals extraction order.
    op.execute("UPDATE extracted_values SET sort_key = position")
    op.add_column("extractions", sa.Column("ocr", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("extractions", "ocr")
    op.drop_column("extracted_values", "sort_key")
    op.drop_column("extracted_values", "origin")
    op.drop_column("value_revisions", "evidence")
