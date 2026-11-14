"""template library, versions, processing runs

Revision ID: c7d2a9e4f1b8
Revises: b41c2e7d9a15
Create Date: 2026-11-13 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d2a9e4f1b8"
down_revision: str | Sequence[str] | None = "b41c2e7d9a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _owned() -> list[sa.Column]:
    return [
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # Template library: templates and the definition of each version.
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=32), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("latest_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source", sa.String(length=64), server_default="blank", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_owned(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_templates_user_id", "templates", ["user_id"])
    op.create_index("ix_templates_team_id", "templates", ["team_id"])
    op.create_table(
        "template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", _JSON, nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        *_owned(),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version"),
    )
    op.create_index("ix_template_versions_template_id", "template_versions", ["template_id"])
    op.create_index("ix_template_versions_user_id", "template_versions", ["user_id"])
    op.create_index("ix_template_versions_team_id", "template_versions", ["team_id"])

    # Documents: template for the next run, number of runs.
    op.add_column("documents", sa.Column("template_version_id", sa.Uuid(), nullable=True))
    op.add_column(
        "documents", sa.Column("discover", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "documents", sa.Column("current_run", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("documents", sa.Column("run_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_documents_template_version_id",
        "documents",
        "template_versions",
        ["template_version_id"],
        ["id"],
    )
    # Extractions: run number, reason, template version.
    op.add_column("extractions", sa.Column("run", sa.Integer(), server_default="1", nullable=False))
    op.add_column("extractions", sa.Column("run_reason", sa.Text(), nullable=True))
    op.add_column("extractions", sa.Column("template_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_extractions_template_version_id",
        "extractions",
        "template_versions",
        ["template_version_id"],
        ["id"],
    )
    op.create_index("ix_extractions_document_run", "extractions", ["document_id", "run"])
    # Values: original label on the document (key-value discovery).
    op.add_column("extracted_values", sa.Column("label", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("extracted_values", "label")
    op.drop_index("ix_extractions_document_run", table_name="extractions")
    op.drop_constraint("fk_extractions_template_version_id", "extractions", type_="foreignkey")
    op.drop_column("extractions", "template_version_id")
    op.drop_column("extractions", "run_reason")
    op.drop_column("extractions", "run")
    op.drop_constraint("fk_documents_template_version_id", "documents", type_="foreignkey")
    op.drop_column("documents", "run_reason")
    op.drop_column("documents", "current_run")
    op.drop_column("documents", "discover")
    op.drop_column("documents", "template_version_id")
    op.drop_index("ix_template_versions_team_id", table_name="template_versions")
    op.drop_index("ix_template_versions_user_id", table_name="template_versions")
    op.drop_index("ix_template_versions_template_id", table_name="template_versions")
    op.drop_table("template_versions")
    op.drop_index("ix_templates_team_id", table_name="templates")
    op.drop_index("ix_templates_user_id", table_name="templates")
    op.drop_table("templates")
