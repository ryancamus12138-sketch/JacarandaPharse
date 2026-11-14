"""ocr_samples.template_key: OCR samples are grouped by template identity, not by its name

Revision ID: e27fca8c26a8
Revises: 249b1c3144ba
Create Date: 2026-11-25 12:00:00.000000"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e27fca8c26a8"
down_revision: str | Sequence[str] | None = "249b1c3144ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ocr_samples") as batch:
        batch.add_column(
            sa.Column("template_key", sa.String(64), nullable=False, server_default="")
        )
        batch.create_index(op.f("ix_ocr_samples_template_key"), ["template_key"])
    # Existing samples: the template of the extraction their field value belongs to. Samples whose
    # value no longer exists keep an empty key and are still grouped by the stored name.
    op.execute(
        sa.text(
            "UPDATE ocr_samples SET template_key = COALESCE(("
            "SELECT extractions.template FROM extracted_values"
            " JOIN extractions ON extractions.id = extracted_values.extraction_id"
            " WHERE extracted_values.id = ocr_samples.value_id), '')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("ocr_samples") as batch:
        batch.drop_index(op.f("ix_ocr_samples_template_key"))
        batch.drop_column("template_key")
