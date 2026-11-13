"""标准模板 Excel（方法一，ADR 0031）：模板 ↔ 一个人能看懂、能改的 Excel。

几张固定的表：模板、字段、表格、规则、对象、参照表（每份参照名单另有一张"参照-名字"）。
按原表导出的样式（工作簿）不在里面：它带着原 Excel 文件，要搬的话用 JSON 导出。
"""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from jacara.plugins.validators import ALL_RULES
from jacara.plugins.validators.base import Rule
from jacara.plugins.validators.rules import REGIONS

FORMAT = "jacarandaparse-template"
TYPES = {"string": "文字", "number": "数字", "date": "日期", "date_range": "日期范围"}
TYPES_BACK = {v: k for k, v in TYPES.items()} | {k: k for k in TYPES}
FIELD_HEADERS = [
    "中文名",
    "字段ID",
    "类型",
    "必填",
    "从单据抽",
    "导出",
    "敏感",
    "可选值",
    "别名",
    "说明",
]
TABLE_HEADERS = ["表ID", "表中文名", "表说明", "至少一行", "导出整张表", *FIELD_HEADERS]
YES, NO = "是", "否"


class StandardError(ValueError):
    pass


def is_standard(data: bytes) -> bool:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True)
    except Exception:
        return False
    if "字段" not in book.sheetnames:
        return False
    header = next(book["字段"].iter_rows(max_row=1, values_only=True), ())
    return "中文名" in header and "字段ID" in header


# ---------- 模板 → Excel ----------


def usable_rule(rule: Rule, template: dict[str, Any]) -> bool:
    """这条内置规则这个模板用得上：要的字段（表格的列）都有；某国的规则只给选了这个国家的模板。"""
    country = REGIONS.get(rule.id)
    if country and country not in (template.get("countries") or []):
        return False
    fields = {f["name"] for f in template.get("fields", [])}
    columns = {t["name"]: {c["name"] for c in t["columns"]} for t in template.get("tables", [])}
    if rule.per_row:
        return all(n in columns.get(rule.per_row, set()) for n in (*rule.needs, *rule.requires))
    for need in (*rule.needs, *rule.requires):
        table, _, column = need.partition(".*.")
        if column and column not in columns.get(table, set()):
            return False
        if not column and need not in fields:
            return False
    return True


def to_excel(template: dict[str, Any]) -> bytes:
    """template：TemplateFile 的字典（title、fields、tables、rules、entities、references……）。"""
    book = Workbook()
    info = book.worksheets[0]
    info.title = "模板"
    _setup(info, ["项目", "内容"], [16, 60])
    for row in (
        ("格式", FORMAT),
        ("格式版本", 1),
        ("模板名", template["title"]),
        ("类型", template.get("category", "")),
        ("功能领域", "、".join(template.get("domains") or [])),
        ("国家和地区", "、".join(template.get("countries") or []) or "通用"),
        ("自定义标签", "、".join(template.get("tags") or [])),
        ("说明", template.get("description", "")),
        ("导出时的版本", template.get("version", 1)),
        (
            "提示",
            "改好之后在“模板”页点“导入模板”选这个文件。"
            "按原表导出的样式不在这里，要搬请用 JSON 导出。",
        ),
    ):
        info.append(list(row))

    fields_ws = book.create_sheet("字段")
    _setup(fields_ws, FIELD_HEADERS, [16, 18, 8, 6, 9, 6, 6, 24, 24, 50])
    for f in template["fields"]:
        fields_ws.append(_field_row(f))
    _choices(fields_ws, len(template["fields"]) + 200, 0)

    tables_ws = book.create_sheet("表格")
    _setup(tables_ws, TABLE_HEADERS, [12, 14, 30, 9, 10, 16, 18, 8, 6, 9, 6, 6, 24, 24, 50])
    for t in template.get("tables", []):
        for c in t["columns"]:
            tables_ws.append(
                [
                    t["name"],
                    t["label"],
                    t.get("description", ""),
                    _yes(t.get("required")),
                    _yes(t.get("export", True)),
                    *_field_row(c),
                ]
            )
    _choices(tables_ws, 500, 5)

    rules_ws = book.create_sheet("规则")
    _setup(rules_ws, ["编号", "名称", "级别", "用"], [22, 46, 8, 6])
    chosen = set(template.get("rules", []))
    # 只列这个模板选了的规则（化验单不会列出发票、收据、机票的规则）。
    # 想加别的规则，在下面空行里写规则编号、"用"填"是"，导入时会检查
    listed = [r for r in ALL_RULES if r.id in chosen]
    for rule in listed:
        rules_ws.append(
            [
                rule.id,
                rule.title,
                "错误" if rule.severity == "error" else "警告",
                _yes(rule.id in chosen),
            ]
        )
    _yes_no(rules_ws, "D", max(len(listed), 30) + 1)  # 留几行空的，导入时可以自己加

    entities_ws = book.create_sheet("对象")
    _setup(entities_ws, ["对象ID", "中文名", "编号字段", "包含的字段"], [14, 14, 16, 50])
    for e in template.get("entities", []):
        entities_ws.append([e["name"], e["label"], e["key"], "、".join(e["fields"])])

    refs_ws = book.create_sheet("参照表")
    _setup(refs_ws, ["参照ID", "中文名", "对照字段", "对照列", "数据在"], [14, 16, 18, 16, 24])
    for r in template.get("references", []):
        sheet_name = _sheet_name(book, f"参照-{r['label']}")
        refs_ws.append(
            [r["name"], r["label"], r.get("match_field", ""), r.get("match_column", ""), sheet_name]
        )
        data_ws = book.create_sheet(sheet_name)
        _setup(data_ws, list(r["columns"]), [16] * len(r["columns"]))
        for row in r.get("rows", []):
            data_ws.append(list(row))

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _sheet_name(book: Workbook, name: str) -> str:
    name = "".join(c for c in name if c not in "[]:*?/\\")[:31]
    candidate, n = name, 2
    while candidate in book.sheetnames:
        candidate = f"{name[:27]}({n})"
        n += 1
    return candidate


def _field_row(f: dict[str, Any]) -> list[Any]:
    return [
        f["label"],
        f["name"],
        TYPES.get(f.get("type", "string"), "文字"),
        _yes(f.get("required")),
        _yes(f.get("extract", True)),
        _yes(f.get("export", True)),
        _yes(f.get("sensitive")),
        "、".join(f.get("options", [])),
        "、".join(f.get("aliases", [])),
        f.get("description", ""),
    ]


def _yes(value: Any) -> str:
    return YES if value else NO


def _setup(ws: Worksheet, headers: list[str], widths: list[int]) -> None:
    from openpyxl.utils import get_column_letter

    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _choices(ws: Worksheet, rows: int, offset: int) -> None:
    """类型、是/否 列加下拉框，填的人不容易写错。offset：前面有几列（表格表多 5 列）。"""
    from openpyxl.utils import get_column_letter

    kinds = DataValidation(type="list", formula1='"文字,数字,日期,日期范围"', allow_blank=True)
    ws.add_data_validation(kinds)
    kinds.add(f"{get_column_letter(offset + 3)}2:{get_column_letter(offset + 3)}{rows}")
    for col in (4, 5, 6, 7):
        _yes_no(ws, get_column_letter(offset + col), rows)
    if offset:
        for col in (4, 5):
            _yes_no(ws, get_column_letter(col), rows)


def _yes_no(ws: Worksheet, letter: str, rows: int) -> None:
    choice = DataValidation(type="list", formula1='"是,否"', allow_blank=True)
    ws.add_data_validation(choice)
    choice.add(f"{letter}2:{letter}{rows}")


# ---------- Excel → 模板 ----------


def from_excel(data: bytes) -> dict[str, Any]:
    """读标准模板 Excel，返回 TemplateFile 的字典。写错的地方说清楚哪张表第几行。"""
    book = load_workbook(io.BytesIO(data), data_only=True)
    info = {str(r[0]).strip(): r[1] for r in _rows(book, "模板") if r and r[0]}
    title = str(info.get("模板名") or "").strip()
    if not title:
        raise StandardError("“模板”表里没有写模板名")

    fields = []
    for counter, (n, row) in enumerate(_numbered(book, "字段", FIELD_HEADERS), start=1):
        fields.append(_field(row, "字段表", n, f"field_{counter}"))

    tables: dict[str, dict[str, Any]] = {}
    for n, row in _numbered(book, "表格", TABLE_HEADERS):
        table_id = _str(row.get("表ID"))
        if not table_id:
            raise StandardError(f"表格表第 {n} 行：没有写表ID")
        table = tables.setdefault(
            table_id,
            {
                "name": table_id,
                "label": _str(row.get("表中文名")) or table_id,
                "description": _str(row.get("表说明")),
                "required": _bool(row.get("至少一行"), False, "表格表", n),
                "export": _bool(row.get("导出整张表"), True, "表格表", n),
                "columns": [],
            },
        )
        table["columns"].append(_field(row, "表格表", n, f"col_{len(table['columns']) + 1}"))

    rules = [
        _str(row.get("编号"))
        for n, row in _numbered(book, "规则", ["编号", "名称", "级别", "用"])
        if _bool(row.get("用"), False, "规则表", n)
    ]
    entities = [
        {
            "name": _str(row.get("对象ID")),
            "label": _str(row.get("中文名")),
            "key": _str(row.get("编号字段")),
            "fields": _list(row.get("包含的字段")),
        }
        for _, row in _numbered(book, "对象", ["对象ID", "中文名", "编号字段", "包含的字段"])
    ]
    references = []
    for n, row in _numbered(book, "参照表", ["参照ID", "中文名", "对照字段", "对照列", "数据在"]):
        sheet = _str(row.get("数据在"))
        if sheet not in book.sheetnames:
            raise StandardError(f"参照表第 {n} 行：找不到放数据的 sheet「{sheet}」")
        values = list(book[sheet].iter_rows(values_only=True))
        columns = [_str(v) for v in values[0] if _str(v)] if values else []
        references.append(
            {
                "name": _str(row.get("参照ID")),
                "label": _str(row.get("中文名")),
                "match_field": _str(row.get("对照字段")),
                "match_column": _str(row.get("对照列")),
                "columns": columns,
                "rows": [
                    [_str(v) for v in r[: len(columns)]]
                    for r in values[1:]
                    if any(_str(v) for v in r)
                ],
            }
        )
    countries_text = _str(info.get("国家和地区"))
    return {
        "format": FORMAT,
        "title": title,
        "category": _str(info.get("类型")),
        # 新导出的才有这三行；旧的 Excel 没有：领域用"类型"，国家按选的规则认
        **({"domains": _list(info.get("功能领域"))} if "功能领域" in info else {}),
        **(
            {"countries": [] if countries_text in ("", "通用") else _list(countries_text)}
            if "国家和地区" in info
            else {}
        ),
        **({"tags": _list(info.get("自定义标签"))} if "自定义标签" in info else {}),
        "description": _str(info.get("说明")),
        "version": int(info.get("导出时的版本") or 1),
        "fields": fields,
        "tables": list(tables.values()),
        "rules": rules,
        "entities": entities,
        "references": references,
    }


def _rows(book: Any, name: str) -> list[tuple[Any, ...]]:
    if name not in book.sheetnames:
        return []
    return list(book[name].iter_rows(values_only=True))


def _numbered(book: Any, name: str, headers: list[str]) -> list[tuple[int, dict[str, Any]]]:
    rows = _rows(book, name)
    if not rows:
        return []
    header = [_str(h) for h in rows[0]]
    needed = {"字段": ["中文名"], "表格": ["表ID", "中文名"]}.get(name, [])
    missing = [h for h in needed if h not in header]
    if missing:
        raise StandardError(f"“{name}”表缺少这几列：{'、'.join(missing)}")
    result = []
    for n, values in enumerate(rows[1:], start=2):
        if not any(_str(v) for v in values):
            continue
        result.append((n, dict(zip(header, values, strict=False))))
    return result


def _field(row: dict[str, Any], where: str, n: int, default_id: str) -> dict[str, Any]:
    label = _str(row.get("中文名"))
    if not label:
        raise StandardError(f"{where}第 {n} 行：没有写中文名")
    kind = TYPES_BACK.get(_str(row.get("类型")) or "文字")
    if kind is None:
        raise StandardError(f"{where}第 {n} 行：类型只能填 文字、数字、日期、日期范围")
    sensitive = _bool(row.get("敏感"), False, where, n)
    return {
        "name": _str(row.get("字段ID")) or default_id,
        "label": label,
        "type": kind,
        "required": _bool(row.get("必填"), False, where, n),
        "extract": _bool(row.get("从单据抽"), True, where, n),
        "export": _bool(row.get("导出"), not sensitive, where, n),
        "sensitive": sensitive,
        "options": _list(row.get("可选值")),
        "aliases": _list(row.get("别名")),
        "description": _str(row.get("说明")),
    }


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _list(value: Any) -> list[str]:
    text = _str(value).replace("，", "、").replace(",", "、").replace("；", "、").replace(";", "、")
    return [v.strip() for v in text.split("、") if v.strip()]


def _bool(value: Any, default: bool, where: str, n: int) -> bool:
    text = _str(value).lower()
    if not text:
        return default
    if text in ("是", "y", "yes", "true", "1", "√", "✓"):
        return True
    if text in ("否", "n", "no", "false", "0", "×"):
        return False
    raise StandardError(f"{where}第 {n} 行：是/否 的格子写了「{value}」，只能填 是 或 否")
