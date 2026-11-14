"""Standard template workbook: template ↔ an Excel workbook people can read and edit.

Fixed sheets: template, fields, tables, rules, subjects, reference tables (plus one
"reference-NAME" sheet per reference list). The original-workbook export layout is not included:
it carries the original workbook file, so use the JSON export to move it.

Sheet names, headers and fixed values (types, yes/no, severity) are written in the interface
language of the request. Import accepts every supported language: each fixed label is compared
with its message id and all of its translations (i18n.variants), so a workbook exported in English
can be imported from the Chinese interface and the other way round."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from jacara.core.i18n import N_, N_c, tr, trc, variants, variants_c
from jacara.plugins.validators import ALL_RULES
from jacara.plugins.validators.base import Rule
from jacara.plugins.validators.rules import REGIONS

FORMAT = "jacarandaparse-template"
TYPES = {
    "string": N_("文字"),
    "number": N_("数字"),
    "date": N_("日期"),
    "date_range": N_("日期范围"),
}
FIELD_HEADERS = [
    N_("中文名"),
    N_("字段ID"),
    N_("类型"),
    N_("必填"),
    N_("从单据抽"),
    N_("导出"),
    N_("敏感"),
    N_("可选值"),
    N_("别名"),
    N_("说明"),
]
TABLE_HEADERS = [
    N_("表ID"),
    N_("表中文名"),
    N_("表说明"),
    N_("至少一行"),
    N_("导出整张表"),
    *FIELD_HEADERS,
]
RULE_HEADERS = [N_("编号"), N_("名称"), N_("级别"), N_("用")]
ENTITY_HEADERS = [N_("对象ID"), N_("中文名"), N_("编号字段"), N_("包含的字段")]
REFERENCE_HEADERS = [N_("参照ID"), N_("中文名"), N_("对照字段"), N_("对照列"), N_("数据在")]
INFO_HEADERS = [N_("项目"), N_("内容")]
SHEETS = {
    "info": N_c("sheet", "模板"),
    "fields": N_c("sheet", "字段"),
    "tables": N_c("sheet", "表格"),
    "rules": N_c("sheet", "规则"),
    "entities": N_c("sheet", "对象"),
    "references": N_c("sheet", "参照表"),
}
"""Fixed sheets → sheet name (message id in the context "sheet")."""
INFO = {
    "format": N_("格式"),
    "format_version": N_("格式版本"),
    "title": N_("模板名"),
    "category": N_("类型"),
    "domains": N_("功能领域"),
    "countries": N_("国家和地区"),
    "tags": N_("自定义标签"),
    "description": N_("说明"),
    "version": N_("导出时的版本"),
    "hint": N_("提示"),
}
YES, NO = N_("是"), N_("否")
GENERIC = N_("通用")
"""Country cell of a template that applies everywhere."""


def _canonical(labels: list[str]) -> dict[str, str]:
    """Written label in any supported language → message id."""
    return {text: label for label in labels for text in variants(label)}


def _type_back(text: str) -> str | None:
    """Type cell (in any language, or the type id itself) → type id."""
    found = {v: k for k, label in TYPES.items() for v in variants(label)} | {k: k for k in TYPES}
    return found.get(text)


def _sheet_title(key: str) -> str:
    """Name of a fixed sheet in the request language."""
    name = SHEETS[key]
    return trc("sheet", name)


def _sheet(book: Any, key: str) -> str | None:
    """Name of a fixed sheet in the workbook, written in any supported language."""
    names = variants_c("sheet", SHEETS[key])
    return next((n for n in book.sheetnames if n in names), None)


class StandardError(ValueError):
    pass


def is_standard(data: bytes) -> bool:
    try:
        book = load_workbook(io.BytesIO(data), read_only=True)
    except Exception:
        return False
    sheet = _sheet(book, "fields")
    if sheet is None:
        return False
    header = {_str(h) for h in next(book[sheet].iter_rows(max_row=1, values_only=True), ())}
    return bool(header & variants(N_("中文名"))) and bool(header & variants(N_("字段ID")))


# ---------- Template → Excel ----------


def usable_rule(rule: Rule, template: dict[str, Any]) -> bool:
    """Whether this built-in rule applies to the template: all required fields (table columns)
    present, and country-specific rules only for templates of that country."""
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
    """template: a TemplateFile dictionary (title, fields, tables, rules, entities, references,
    …)."""
    book = Workbook()
    info = book.worksheets[0]
    info.title = _sheet_title("info")
    _setup(info, INFO_HEADERS, [16, 60])
    separator = tr("、")
    for key, value in (
        ("format", FORMAT),
        ("format_version", 1),
        ("title", template["title"]),
        ("category", template.get("category", "")),
        ("domains", separator.join(template.get("domains") or [])),
        ("countries", separator.join(template.get("countries") or []) or tr(GENERIC)),
        ("tags", separator.join(template.get("tags") or [])),
        ("description", template.get("description", "")),
        ("version", template.get("version", 1)),
        (
            "hint",
            tr(
                "改好之后在“模板”页点“导入模板”选这个文件。"
                "按原表导出的样式不在这里，要搬请用 JSON 导出。"
            ),
        ),
    ):
        info.append([tr(INFO[key]), value])

    fields_ws = book.create_sheet(_sheet_title("fields"))
    _setup(fields_ws, FIELD_HEADERS, [16, 18, 8, 6, 9, 6, 6, 24, 24, 50])
    for f in template["fields"]:
        fields_ws.append(_field_row(f))
    _choices(fields_ws, len(template["fields"]) + 200, 0)

    tables_ws = book.create_sheet(_sheet_title("tables"))
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

    rules_ws = book.create_sheet(_sheet_title("rules"))
    _setup(rules_ws, RULE_HEADERS, [22, 46, 8, 6])
    chosen = set(template.get("rules", []))
    # List only the rules this template selected (a lab report does not list invoice, receipt or air
    # ticket rules). To add a rule, enter its id in an empty row below and set "use" to "yes"; it is
    # validated on import.
    listed = [r for r in ALL_RULES if r.id in chosen]
    for rule in listed:
        rules_ws.append(
            [
                rule.id,
                tr(rule.title),
                tr("错误") if rule.severity == "error" else tr("警告"),
                _yes(rule.id in chosen),
            ]
        )
    # Leave a few empty rows for additions before import.
    _yes_no(rules_ws, "D", max(len(listed), 30) + 1)

    entities_ws = book.create_sheet(_sheet_title("entities"))
    _setup(entities_ws, ENTITY_HEADERS, [14, 14, 16, 50])
    for e in template.get("entities", []):
        entities_ws.append([e["name"], e["label"], e["key"], separator.join(e["fields"])])

    refs_ws = book.create_sheet(_sheet_title("references"))
    _setup(refs_ws, REFERENCE_HEADERS, [14, 16, 18, 16, 24])
    for r in template.get("references", []):
        sheet_name = _sheet_name(book, tr("参照-{label}", label=r["label"]))
        refs_ws.append(
            [r["name"], r["label"], r.get("match_field", ""), r.get("match_column", ""), sheet_name]
        )
        data_ws = book.create_sheet(sheet_name)
        _setup(data_ws, list(r["columns"]), [16] * len(r["columns"]), translate=False)
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
    separator = tr("、")
    return [
        f["label"],
        f["name"],
        tr(TYPES.get(f.get("type", "string"), TYPES["string"])),
        _yes(f.get("required")),
        _yes(f.get("extract", True)),
        _yes(f.get("export", True)),
        _yes(f.get("sensitive")),
        separator.join(f.get("options", [])),
        separator.join(f.get("aliases", [])),
        f.get("description", ""),
    ]


def _yes(value: Any) -> str:
    return tr(YES) if value else tr(NO)


def _setup(ws: Worksheet, headers: list[str], widths: list[int], translate: bool = True) -> None:
    """Header row (fixed headers are message ids, translated; reference tables keep their own
    column names), bold, frozen, with column widths."""
    from openpyxl.utils import get_column_letter

    ws.append([tr(h) for h in headers] if translate else headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _choices(ws: Worksheet, rows: int, offset: int) -> None:
    """Add drop-downs to type and yes/no columns to prevent input errors. offset: number of
    preceding columns (the tables sheet has 5 more)."""
    from openpyxl.utils import get_column_letter

    kinds = DataValidation(
        type="list", formula1=f'"{",".join(tr(t) for t in TYPES.values())}"', allow_blank=True
    )
    ws.add_data_validation(kinds)
    kinds.add(f"{get_column_letter(offset + 3)}2:{get_column_letter(offset + 3)}{rows}")
    for col in (4, 5, 6, 7):
        _yes_no(ws, get_column_letter(offset + col), rows)
    if offset:
        for col in (4, 5):
            _yes_no(ws, get_column_letter(col), rows)


def _yes_no(ws: Worksheet, letter: str, rows: int) -> None:
    choice = DataValidation(type="list", formula1=f'"{tr(YES)},{tr(NO)}"', allow_blank=True)
    ws.add_data_validation(choice)
    choice.add(f"{letter}2:{letter}{rows}")


# ---------- Excel → template ----------


def from_excel(data: bytes) -> dict[str, Any]:
    """Read a standard template workbook and return a TemplateFile dictionary. Errors name the sheet
    and row."""
    book = load_workbook(io.BytesIO(data), data_only=True)
    names = _canonical(list(INFO.values()))
    info = {names.get(_str(r[0]), _str(r[0])): r[1] for r in _rows(book, "info") if r and r[0]}
    title = str(info.get(INFO["title"]) or "").strip()
    if not title:
        raise StandardError(tr("“模板”表里没有写模板名"))

    fields_sheet = tr("字段表")
    fields = []
    for counter, (n, row) in enumerate(_numbered(book, "fields", FIELD_HEADERS), start=1):
        fields.append(_field(row, fields_sheet, n, f"field_{counter}"))

    tables_sheet = tr("表格表")
    tables: dict[str, dict[str, Any]] = {}
    for n, row in _numbered(book, "tables", TABLE_HEADERS):
        table_id = _str(row.get(N_("表ID")))
        if not table_id:
            raise StandardError(tr("表格表第 {n} 行：没有写表ID", n=n))
        table = tables.setdefault(
            table_id,
            {
                "name": table_id,
                "label": _str(row.get(N_("表中文名"))) or table_id,
                "description": _str(row.get(N_("表说明"))),
                "required": _bool(row.get(N_("至少一行")), False, tables_sheet, n),
                "export": _bool(row.get(N_("导出整张表")), True, tables_sheet, n),
                "columns": [],
            },
        )
        table["columns"].append(_field(row, tables_sheet, n, f"col_{len(table['columns']) + 1}"))

    rules_sheet = tr("规则表")
    rules = [
        _str(row.get(N_("编号")))
        for n, row in _numbered(book, "rules", RULE_HEADERS)
        if _bool(row.get(N_("用")), False, rules_sheet, n)
    ]
    entities = [
        {
            "name": _str(row.get(N_("对象ID"))),
            "label": _str(row.get(N_("中文名"))),
            "key": _str(row.get(N_("编号字段"))),
            "fields": _list(row.get(N_("包含的字段"))),
        }
        for _, row in _numbered(book, "entities", ENTITY_HEADERS)
    ]
    references = []
    for n, row in _numbered(book, "references", REFERENCE_HEADERS):
        sheet = _str(row.get(N_("数据在")))
        if sheet not in book.sheetnames:
            raise StandardError(
                tr("参照表第 {n} 行：找不到放数据的 sheet「{sheet}」", n=n, sheet=sheet)
            )
        values = list(book[sheet].iter_rows(values_only=True))
        columns = [_str(v) for v in values[0] if _str(v)] if values else []
        references.append(
            {
                "name": _str(row.get(N_("参照ID"))),
                "label": _str(row.get(N_("中文名"))),
                "match_field": _str(row.get(N_("对照字段"))),
                "match_column": _str(row.get(N_("对照列"))),
                "columns": columns,
                "rows": [
                    [_str(v) for v in r[: len(columns)]]
                    for r in values[1:]
                    if any(_str(v) for v in r)
                ],
            }
        )
    countries_text = _str(info.get(INFO["countries"]))
    generic = {"", *variants(GENERIC)}
    return {
        "format": FORMAT,
        "title": title,
        "category": _str(info.get(INFO["category"])),
        # Only newly exported workbooks have these three rows; for older ones, the domain comes from
        # "type" and the country from the selected rules.
        **({"domains": _list(info.get(INFO["domains"]))} if INFO["domains"] in info else {}),
        **(
            {"countries": [] if countries_text in generic else _list(countries_text)}
            if INFO["countries"] in info
            else {}
        ),
        **({"tags": _list(info.get(INFO["tags"]))} if INFO["tags"] in info else {}),
        "description": _str(info.get(INFO["description"])),
        "version": int(info.get(INFO["version"]) or 1),
        "fields": fields,
        "tables": list(tables.values()),
        "rules": rules,
        "entities": entities,
        "references": references,
    }


def _rows(book: Any, key: str) -> list[tuple[Any, ...]]:
    name = _sheet(book, key)
    if name is None:
        return []
    return list(book[name].iter_rows(values_only=True))


REQUIRED_COLUMNS = {"fields": [N_("中文名")], "tables": [N_("表ID"), N_("中文名")]}
"""Columns a sheet must have (message ids)."""


def _numbered(book: Any, key: str, headers: list[str]) -> list[tuple[int, dict[str, Any]]]:
    """Data rows of a fixed sheet, keyed by header message id, with their row numbers."""
    rows = _rows(book, key)
    if not rows:
        return []
    names = _canonical(headers)
    header = [names.get(_str(h), _str(h)) for h in rows[0]]
    missing = [h for h in REQUIRED_COLUMNS.get(key, []) if h not in header]
    if missing:
        raise StandardError(
            tr(
                "“{sheet}”表缺少这几列：{columns}",
                sheet=_sheet_title(key),
                columns=tr("、").join(tr(h) for h in missing),
            )
        )
    result = []
    for n, values in enumerate(rows[1:], start=2):
        if not any(_str(v) for v in values):
            continue
        result.append((n, dict(zip(header, values, strict=False))))
    return result


def _field(row: dict[str, Any], where: str, n: int, default_id: str) -> dict[str, Any]:
    label = _str(row.get(N_("中文名")))
    if not label:
        raise StandardError(tr("{sheet}第 {n} 行：没有写中文名", sheet=where, n=n))
    written = _str(row.get(N_("类型")))
    kind = _type_back(written) if written else "string"
    if kind is None:
        raise StandardError(
            tr(
                "{sheet}第 {n} 行：类型只能填 {types}",
                sheet=where,
                n=n,
                types=tr("、").join(tr(t) for t in TYPES.values()),
            )
        )
    sensitive = _bool(row.get(N_("敏感")), False, where, n)
    return {
        "name": _str(row.get(N_("字段ID"))) or default_id,
        "label": label,
        "type": kind,
        "required": _bool(row.get(N_("必填")), False, where, n),
        "extract": _bool(row.get(N_("从单据抽")), True, where, n),
        "export": _bool(row.get(N_("导出")), not sensitive, where, n),
        "sensitive": sensitive,
        "options": _list(row.get(N_("可选值"))),
        "aliases": _list(row.get(N_("别名"))),
        "description": _str(row.get(N_("说明"))),
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
    if text in {v.lower() for v in variants(YES)} | {"y", "yes", "true", "1", "√", "✓"}:
        return True
    if text in {v.lower() for v in variants(NO)} | {"n", "no", "false", "0", "×"}:
        return False
    raise StandardError(
        tr(
            "{sheet}第 {n} 行：是/否 的格子写了「{value}」，只能填 {yes} 或 {no}",
            sheet=where,
            n=n,
            value=value,
            yes=tr(YES),
            no=tr(NO),
        )
    )
