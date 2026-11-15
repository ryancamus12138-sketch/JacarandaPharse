"""ocr samples and team settings

Revision ID: e9b4d2f7a1c3
Revises: d7a2c9e4b1f6
Create Date: 2026-11-19 12:00:00.000000"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9b4d2f7a1c3"
down_revision: str | Sequence[str] | None = "d7a2c9e4b1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Per-team settings (currently only "collect OCR correction samples").
    op.create_table(
        "team_settings",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", JSON, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("team_id", "key", name=op.f("pk_team_settings")),
    )
    # OCR correction samples: line crop, ground-truth transcription and the original OCR reading.
    op.create_table(
        "ocr_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("value_id", sa.Uuid(), nullable=True),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("polygon", JSON, nullable=False),
        sa.Column("transcription", sa.Text(), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("error_tags", JSON, nullable=False),
        sa.Column("template", sa.String(length=200), nullable=False),
        sa.Column("field", sa.String(length=200), nullable=False),
        sa.Column("field_label", sa.String(length=200), nullable=False),
        sa.Column("source_format", sa.String(length=8), nullable=False),
        sa.Column("ocr_engine", sa.String(length=128), nullable=False),
        sa.Column("crop_key", sa.String(length=255), nullable=True),
        sa.Column("crop_sha256", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_ocr_samples_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ocr_samples")),
    )
    op.create_index(op.f("ix_ocr_samples_document_id"), "ocr_samples", ["document_id"])
    op.create_index(op.f("ix_ocr_samples_crop_sha256"), "ocr_samples", ["crop_sha256"])
    op.create_index(op.f("ix_ocr_samples_user_id"), "ocr_samples", ["user_id"])
    op.create_index(op.f("ix_ocr_samples_team_id"), "ocr_samples", ["team_id"])


def downgrade() -> None:
    op.drop_table("ocr_samples")
    op.drop_table("team_settings")
