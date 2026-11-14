"""ocr sample groups and splits

Revision ID: a4d8e1c6b9f2
Revises: f3c7a9e2d5b8
Create Date: 2026-11-21 12:00:00.000000"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d8e1c6b9f2"
down_revision: str | Sequence[str] | None = "f3c7a9e2d5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _owned() -> list[sa.Column]:
    return [
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    # Business document of each sample (file and first page) and the number of transcription
    # revisions.
    op.add_column(
        "ocr_samples", sa.Column("unit_page", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "ocr_samples", sa.Column("revision", sa.Integer(), server_default="1", nullable=False)
    )
    # Business document to group mapping.
    op.create_table(
        "doc_group_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("first_page", sa.Integer(), nullable=False),
        *_owned(),
        _created(),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "first_page"),
    )
    op.create_index("ix_doc_group_members_group_id", "doc_group_members", ["group_id"])
    op.create_index("ix_doc_group_members_document_id", "doc_group_members", ["document_id"])
    op.create_index("ix_doc_group_members_user_id", "doc_group_members", ["user_id"])
    op.create_index("ix_doc_group_members_team_id", "doc_group_members", ["team_id"])
    # Split of each group within a split version.
    op.create_table(
        "split_assignments",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("split", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _created(),
        sa.PrimaryKeyConstraint("team_id", "version", "group_id"),
    )
    # Confirmed decisions on whether two documents are the same.
    op.create_table(
        "doc_pair_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("a_document", sa.Uuid(), nullable=False),
        sa.Column("a_page", sa.Integer(), nullable=False),
        sa.Column("b_document", sa.Uuid(), nullable=False),
        sa.Column("b_page", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        *_owned(),
        _created(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doc_pair_decisions_a_document", "doc_pair_decisions", ["a_document"])
    op.create_index("ix_doc_pair_decisions_b_document", "doc_pair_decisions", ["b_document"])
    op.create_index("ix_doc_pair_decisions_user_id", "doc_pair_decisions", ["user_id"])
    op.create_index("ix_doc_pair_decisions_team_id", "doc_pair_decisions", ["team_id"])
    # Samples included in each export.
    op.create_table(
        "ocr_sample_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("split_version", sa.Integer(), nullable=False),
        sa.Column("filters", JSON, nullable=False),
        sa.Column("counts", JSON, nullable=False),
        sa.Column("items", JSON, nullable=False),
        sa.Column("affected", JSON, nullable=False),
        *_owned(),
        _created(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocr_sample_exports_user_id", "ocr_sample_exports", ["user_id"])
    op.create_index("ix_ocr_sample_exports_team_id", "ocr_sample_exports", ["team_id"])


def downgrade() -> None:
    op.drop_table("ocr_sample_exports")
    op.drop_table("doc_pair_decisions")
    op.drop_table("split_assignments")
    op.drop_table("doc_group_members")
    op.drop_column("ocr_samples", "revision")
    op.drop_column("ocr_samples", "unit_page")
