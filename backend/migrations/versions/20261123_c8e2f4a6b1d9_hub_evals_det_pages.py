"""dataset library, OCR model evaluations, detection samples (ADR 0059, 0060, 0061)

Revision ID: c8e2f4a6b1d9
Revises: b6f1c3d8e2a5
Create Date: 2026-11-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8e2f4a6b1d9"
down_revision: str | Sequence[str] | None = "b6f1c3d8e2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON(none_as_null=True).with_variant(
        postgresql.JSONB(none_as_null=True), "postgresql"
    )


def _owned_indexes(table: str) -> None:
    op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    op.create_index(f"ix_{table}_team_id", table, ["team_id"])


def upgrade() -> None:
    # 数据集库（ADR 0059）：zip 在对象存储，这里记名字、版本、种类、每边几条
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("counts", _json(), nullable=False),
        sa.Column("groups", _json(), nullable=False),
        sa.Column("files", _json(), nullable=False),
        sa.Column("problems", _json(), nullable=False),
        sa.Column("export_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "name", "version"),
    )
    _owned_indexes("datasets")
    # OCR 模型评测（ADR 0060）
    op.create_table(
        "ocr_eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("split", sa.String(8), nullable=False),
        sa.Column("task", sa.String(8), nullable=False),
        sa.Column("arms", _json(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("progress", sa.String(64), nullable=False),
        sa.Column("summary", _json(), nullable=True),
        sa.Column("lines_key", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_eval_runs_dataset_id", "ocr_eval_runs", ["dataset_id"])
    _owned_indexes("ocr_eval_runs")
    # 检测样本（ADR 0061）：一页上所有的文字框
    op.create_table(
        "det_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), sa.ForeignKey("extractions.id"), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("unit_page", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("boxes", _json(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("ocr_engine", sa.String(255), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page"),
    )
    op.create_index("ix_det_pages_document_id", "det_pages", ["document_id"])
    _owned_indexes("det_pages")


def downgrade() -> None:
    op.drop_table("det_pages")
    op.drop_table("ocr_eval_runs")
    op.drop_table("datasets")
