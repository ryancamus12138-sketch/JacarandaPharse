"""profiles, skipped documents, comments

Revision ID: d3e8b1f6a2c4
Revises: c7d2a9e4f1b8
Create Date: 2026-11-14 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e8b1f6a2c4"
down_revision: str | Sequence[str] | None = "c7d2a9e4f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    # 个人资料（头像、个人模式下的昵称）：每人一行
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("avatar_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_profiles_team_id", "profiles", ["team_id"])

    # 审核时先跳过的文档
    op.add_column("documents", sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("skip_reason", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("skipped_by", sa.Uuid(), nullable=True))

    # 备注：留给同事的话，可以挂在整份文档或某个字段上
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("field_name", sa.String(length=80), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comments_document_id", "comments", ["document_id"])
    op.create_index("ix_comments_user_id", "comments", ["user_id"])
    op.create_index("ix_comments_team_id", "comments", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_comments_team_id", table_name="comments")
    op.drop_index("ix_comments_user_id", table_name="comments")
    op.drop_index("ix_comments_document_id", table_name="comments")
    op.drop_table("comments")
    op.drop_column("documents", "skipped_by")
    op.drop_column("documents", "skip_reason")
    op.drop_column("documents", "skipped_at")
    op.drop_index("ix_profiles_team_id", table_name="profiles")
    op.drop_table("profiles")
