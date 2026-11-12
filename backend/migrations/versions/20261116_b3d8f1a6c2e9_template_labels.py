"""template labels: domains, countries, custom tags

Revision ID: b3d8f1a6c2e9
Revises: a7c4e2f9d1b3
Create Date: 2026-11-16 12:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3d8f1a6c2e9"
down_revision: str | Sequence[str] | None = "a7c4e2f9d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

# 写迁移时（2026-11-16）各国规则的编号，不从程序代码里引用：以后代码改了，这个迁移的结果也不变
_RULE_COUNTRIES = {
    **dict.fromkeys(
        (
            "cn_invoice_no",
            "cn_tax_balance",
            "cn_items_amount",
            "cn_items_tax",
            "cn_words",
            "fare_tax_balance",
            "air_total",
            "air_vat",
        ),
        "CN",
    ),
    **dict.fromkeys(
        (
            "au_buyer_1000",
            "seller_abn",
            "buyer_abn",
            "au_tax_invoice_title",
            "au_gst_eleventh",
            "merchant_abn",
            "ato_evidence",
            "ato_gst_credit",
            "gst_eleventh",
        ),
        "AU",
    ),
}
_OLD_REGIONS = {"cn": ["CN"], "au": ["AU"], "": []}


def upgrade() -> None:
    op.add_column("templates", sa.Column("domains", _JSON, server_default="[]", nullable=False))
    op.add_column("templates", sa.Column("countries", _JSON, server_default="[]", nullable=False))
    op.add_column("templates", sa.Column("tags", _JSON, server_default="[]", nullable=False))

    # 原来的"类型"变成第一个功能领域；国家看最新版定义里的 region，没有就看选的规则
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT t.id, t.category, v.definition FROM templates t "
            "JOIN template_versions v ON v.template_id = t.id AND v.version = t.latest_version"
        )
    ).all()
    for template_id, category, definition in rows:
        data = definition if isinstance(definition, dict) else json.loads(definition or "{}")
        if "region" in data and data["region"] in _OLD_REGIONS:
            countries = _OLD_REGIONS[data["region"]]
        else:
            countries = sorted(
                {_RULE_COUNTRIES[r] for r in data.get("rules", []) if r in _RULE_COUNTRIES}
            )
        domains = [category] if category else []
        conn.execute(
            sa.text("UPDATE templates SET domains = :d, countries = :c WHERE id = :id").bindparams(
                sa.bindparam("d", type_=_JSON), sa.bindparam("c", type_=_JSON)
            ),
            {"d": domains, "c": countries, "id": template_id},
        )


def downgrade() -> None:
    op.drop_column("templates", "tags")
    op.drop_column("templates", "countries")
    op.drop_column("templates", "domains")
