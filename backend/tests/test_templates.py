"""模板：templates/*.toml 能读、写错了说清楚哪里错、存库时记下模板版本、接口能查。"""

from __future__ import annotations

import dataclasses

import pytest

from jacara.core.db import session_scope
from jacara.models import Document
from jacara.pipeline.load import load_reports
from jacara.pipeline.store import save_document
from jacara.templates import TemplateError, load_template, parse_template, template_names


def test_builtin_templates_all_load():
    assert {"receipt", "receipt_full"} <= set(template_names())
    for name in template_names():
        template = load_template(name)
        assert template.name == name and template.version >= 1 and template.fields


def test_receipt_template_is_what_v1_used():
    """v1 的字段清单原样搬进了 receipt.toml：字段、顺序、给模型的说明都没变，抽取结果才会一样。"""
    receipt = load_template("receipt")
    assert [f.name for f in receipt.fields] == ["merchant", "document_no", "date", "total"]
    assert [f.label for f in receipt.fields] == ["商家", "单号", "日期", "总额"]
    assert receipt.specs[3].description == "最终应付总额（含税、四舍五入之后的金额）"
    assert [r.id for r in receipt.rules] == [
        "total_positive",
        "date_exists",
        "date_not_future",
        "rounding",
        "cash_covers_total",
        "change",
    ]


def test_full_template_starts_with_the_receipt_fields():
    full, receipt = load_template("receipt_full"), load_template("receipt")
    assert len(full.fields) == 14
    assert full.fields[:4] == receipt.fields


def test_unknown_template_lists_what_exists():
    with pytest.raises(TemplateError, match="receipt_full"):
        load_template("invoice_x")


def _raw(**changes):
    raw = {
        "name": "demo",
        "version": 1,
        "rules": ["total_positive"],
        "fields": [{"name": "total", "label": "总额", "type": "number"}],
    }
    return {**raw, **changes}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "other"}, "name 应该是 demo"),
        ({"version": 0}, "version"),
        ({"version": "1"}, "version"),
        ({"fields": []}, "至少要有一个字段"),
        ({"fields": [{"name": "total", "type": "money"}]}, "type 只能是"),
        ({"fields": [{"name": "总额", "type": "number"}]}, "英文字母"),
        ({"fields": [{"name": "a", "type": "string"}, {"name": "a", "type": "string"}]}, "重复"),
        ({"rules": ["no_such_rule"]}, "no_such_rule"),
    ],
)
def test_bad_template_says_what_is_wrong(changes, message):
    with pytest.raises(TemplateError, match=message):
        parse_template(_raw(**changes), "demo")


def test_template_defaults():
    template = parse_template(_raw(), "demo")
    assert template.title == "demo"
    assert template.fields[0].required is False


def test_template_version_is_saved_and_read_back(factory, report, tmp_path):
    receipt = tmp_path / "receipt.jpg"
    receipt.write_bytes(b"not really a jpeg")
    report = dataclasses.replace(report, template="receipt", template_version=3)
    with session_scope(factory) as session:
        document_id = save_document(session, receipt, [report]).id
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        assert document.extractions[0].template_version == 3
        [loaded] = load_reports(document)
        assert loaded.template == "receipt" and loaded.template_version == 3


def test_templates_api(client):
    response = client.get("/api/v1/templates")
    assert response.status_code == 200
    receipt = next(t for t in response.json() if t["name"] == "receipt")
    assert receipt["version"] == 1
    assert receipt["fields"][0] == {
        "name": "merchant",
        "label": "商家",
        "type": "string",
        "required": True,
        "description": load_template("receipt").fields[0].description,
    }
    assert {"id": "change", "title": "现金 − 总额 = 找零"}.items() <= receipt["rules"][-1].items()

    assert client.get("/api/v1/templates/receipt_full").json()["version"] == 1
    missing = client.get("/api/v1/templates/nope")
    assert missing.status_code == 404
    assert "receipt" in missing.json()["detail"]
