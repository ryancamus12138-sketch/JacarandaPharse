"""导出 JSON：给别的程序读。内容和 Excel 一样（审核之后的值，模板里设成不导出的不写），
结构是嵌套的。

    {
      "format": "jacarandaparse.export.v1",
      "exported_at": "2026-11-13T19:30:00+11:00",
      "documents": [
        {
          "id": "…", "filename": "receipt.jpg", "status": "reviewed",
          "units": [                         # 一个文件里的每张单据
            {
              "pages": [1, 1], "template": "receipt", "template_version": 3,
              "fields": {"total": 9.0, "date": "2018-12-25"},
              "tables": {"items": [{"name": "PC", "amount": 9.0}]},
              "labels": {"total": "总额", "items": "商品明细", "items.amount": "金额"},
              "needs_review": ["change"],     # 还等人确认的字段
              "issues": [{"rule": "…", "message": "…", "fields": ["change"]}]
            }
          ]
        }
      ]
    }

数字是数字（9.0），日期是 YYYY-MM-DD 文字，日期范围按模板的设置；表格按显示顺序从第 1 行排。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from jacara.plugins.exporters.base import DocumentReport
from jacara.plugins.exporters.excel import _fields, _reasons, cell_value
from jacara.plugins.extractors.base import split_cell_name

FORMAT = "jacarandaparse.export.v1"


def _plain(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def unit_json(report: DocumentReport) -> dict[str, Any]:
    """一张单据。"""
    fields: dict[str, object] = {}
    tables: dict[str, dict[int, dict[str, object]]] = {}
    pending: list[str] = []
    for a in _fields(report):
        review = report.reviews.get(a.field.name)
        value = _plain(cell_value(a, review, report))
        cell = split_cell_name(a.field.name)
        if cell is None:
            fields[a.field.name] = value
        else:
            tables.setdefault(cell[0], {}).setdefault(cell[1], {})[cell[2]] = value
        if _reasons(a, report) and review is None:
            pending.append(a.field.name)
    return {
        "pages": [report.page, report.last_page or report.page],
        "template": report.template,
        "template_version": report.template_version,
        "fields": fields,
        "tables": {name: [rows[i] for i in sorted(rows)] for name, rows in tables.items()},
        "labels": dict(report.labels),
        "needs_review": pending,
        "issues": [
            {"rule": i.rule, "message": i.message, "fields": list(i.fields)}
            for i in report.checks.issues
        ],
    }


def to_json(
    documents: Sequence[tuple[dict[str, Any], Sequence[DocumentReport]]],
    exported_at: datetime,
) -> bytes:
    """[(文档的基本信息, 它的每张单据)] → JSON（UTF-8，缩进两格，中文不转义）。"""
    content = {
        "format": FORMAT,
        "exported_at": exported_at.isoformat(timespec="seconds"),
        "documents": [
            {**info, "units": [unit_json(r) for r in reports]} for info, reports in documents
        ],
    }
    return json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
