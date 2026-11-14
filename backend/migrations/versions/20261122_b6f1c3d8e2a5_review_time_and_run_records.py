"""review time, OCR snapshots, model input, error attributions, sample origin

Revision ID: b6f1c3d8e2a5
Revises: a4d8e1c6b9f2
Create Date: 2026-11-22 12:00:00.000000"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6f1c3d8e2a5"
down_revision: str | Sequence[str] | None = "a4d8e1c6b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON(none_as_null=True).with_variant(
        postgresql.JSONB(none_as_null=True), "postgresql"
    )


def upgrade() -> None:
    # Active review time in seconds (review page open and in use).
    op.add_column(
        "documents", sa.Column("review_seconds", sa.Integer(), server_default="0", nullable=False)
    )
    # Model input of each run and the location of the OCR snapshot.
    op.add_column("extractions", sa.Column("model_input", _json(), nullable=True))
    op.add_column("extractions", sa.Column("ocr_snapshot", sa.String(512), nullable=True))
    # Error attribution: the pipeline stage responsible for each corrected value.
    op.create_table(
        "error_attributions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("team_id", sa.Uuid(), nullable=False, index=True),
        sa.Column(
            "value_id", sa.Uuid(), sa.ForeignKey("extracted_values.id"), nullable=False, unique=True
        ),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False, index=True
        ),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("evidence_refs", _json(), nullable=False),
        sa.Column("attributor_version", sa.String(16), nullable=False),
        sa.Column("override_stage", sa.String(16), nullable=True),
        sa.Column("override_note", sa.Text(), nullable=False),
        sa.Column("override_by", sa.Uuid(), nullable=True),
        sa.Column("overridden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    # OCR samples backfilled from earlier corrections (require manual verification).
    op.add_column(
        "ocr_samples",
        sa.Column("origin", sa.String(12), server_default="review", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ocr_samples", "origin")
    op.drop_table("error_attributions")
    op.drop_column("extractions", "ocr_snapshot")
    op.drop_column("extractions", "model_input")
    op.drop_column("documents", "review_seconds")
