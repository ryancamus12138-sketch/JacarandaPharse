"""progress, processed/reviewed time, export records

Revision ID: b41c2e7d9a15
Revises: 7a3d9e1f4b62
Create Date: 2026-11-12 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b41c2e7d9a15"
down_revision: str | Sequence[str] | None = "7a3d9e1f4b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # 工作台（ADR 0029）：处理进度、处理完和审核完的时间
    op.add_column("documents", sa.Column("stage", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("progress", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    # 已经处理完的文档没有记时间：用上传时间代替，工作台的"今天"统计不会把它们算进去
    op.execute(
        "UPDATE documents SET processed_at = created_at"
        " WHERE status IN ('done', 'failed', 'reviewed')"
    )
    # 导出记录：每次导出的文件存进对象存储，可以重新下载
    op.create_table(
        "exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("documents", _JSON, nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exports_user_id", "exports", ["user_id"])
    op.create_index("ix_exports_team_id", "exports", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_exports_team_id", table_name="exports")
    op.drop_index("ix_exports_user_id", table_name="exports")
    op.drop_table("exports")
    op.drop_column("documents", "reviewed_at")
    op.drop_column("documents", "processed_at")
    op.drop_column("documents", "progress")
    op.drop_column("documents", "stage")
