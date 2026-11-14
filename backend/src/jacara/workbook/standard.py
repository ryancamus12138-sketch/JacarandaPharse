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
import re
from dataclasses import dataclass, field
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from jacara.core.i18n import N_, N_c, tr, trc, variants, variants_c
from jacara.core.i18n import language as ui_language
from jacara.plugins.validators import ALL_RULES
from jacara.plugins.validators.base import Rule
from jacara.plugins.validators.rules import REGIONS
from jacara.templates import domain_label

FORMAT = "jacarandaparse-template"
FORMAT_VERSION = 1
"""Layout version written to the template sheet. A workbook of a newer version is refused, because
columns it adds would otherwise be dropped silently. When the layout changes, the version goes up
by one: new columns are appended with defaults (so workbooks of earlier versions are read), renamed
headers go into FORMER and removed ones into RETIRED."""
TYPES = {
    "string": N_("文字"),
    "number": N_("数字"),
    "date": N_("日期"),
    "date_range": N_("日期范围"),
}
FIELD_HEADERS = [
    N_("名称"),
    N_("字段 ID"),
    N_("类型"),
    N_("必填"),
    N_("从单据抽取"),
    N_("导出"),
    N_("敏感"),
    N_("可选值"),
    N_("别名"),
    N_("说明"),
    N_("公式"),
    N_("不能重复"),
    N_("日期范围导出"),
    N_("分隔符"),
]
"""Columns of the fields sheet; later layout versions append new columns so existing ones keep their
positions."""
TABLE_HEADERS = [
    N_("表格 ID"),
    N_("表格名称"),
    N_("表格说明"),
    N_("至少一行"),
    N_("导出整张表格"),
    *FIELD_HEADERS,
]
RULE_HEADERS = [N_("编号"), N_("名称"), N_("级别"), N_("启用"), N_("条件"), N_("逐行检查的表格")]
"""Built-in rules have an id; custom rules have a condition instead."""
ENTITY_HEADERS = [N_("对象 ID"), N_("名称"), N_("编号字段"), N_("包含字段")]
REFERENCE_HEADERS = [
    N_("参照表 ID"),
    N_("名称"),
    N_("对照字段"),
    N_("对照列"),
    N_("数据所在 sheet"),
]
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
    "title": N_("模板名称"),
    "category": N_("类型"),
    "domains": N_("功能领域"),
    "countries": N_("国家和地区"),
    "tags": N_("自定义标签"),
    "description": N_("说明"),
    "language": N_("主语言"),
    "version": N_("导出时版本"),
    "hint": N_("提示"),
}
LANGUAGE_HEADERS = {
    "label": N_("名称（{lang}）"),
    "description": N_("说明（{lang}）"),
    "table_label": N_("表格名称（{lang}）"),
    "table_description": N_("表格说明（{lang}）"),
    "title": N_("模板名称（{lang}）"),
}
"""Columns (and template sheet rows) holding a text in another language than the primary one."""
RANGE_EXPORTS = {
    "normalized": N_("统一格式"),
    "original": N_("原文"),
    "columns": N_("拆分为两列"),
}
SEVERITIES = {"error": N_("错误"), "warning": N_("警告")}
DEFAULT_SEPARATOR = " ~ "
YES, NO = N_("是"), N_("否")
GENERIC = N_("通用")
"""Country cell of a template that applies everywhere."""


FORMER: dict[str, tuple[str, ...]] = {}
"""Label → labels that earlier layout versions wrote for it (renamed headers). Each new layout
version that renames a header adds an entry here, so workbooks of earlier versions are still read;
each former label is accepted in every supported language. Empty in layout 1."""


RETIRED: tuple[str, ...] = ()
"""Labels of columns that a later layout version removed. Such columns are recognized and skipped
without a note, and their names are never reused for a new column. Empty in layout 1."""


def _written(label: str) -> frozenset[str]:
    """Every way a label may appear in a workbook: in any supported language, current or former."""
    found = set(variants(label))
    for former in FORMER.get(label, ()):
        found |= variants(former)
    return frozenset(found)


def _canonical(labels: list[str]) -> dict[str, str]:
    """Written label in any supported language, current or former → message id."""
    return {text: label for label in labels for text in _written(label)}


_LANGUAGE_CODE = r"([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)"


def _parens(text: str) -> str:
    """Full-width and ASCII parentheses are treated alike (both are typed by hand)."""
    return text.replace("（", "(").replace("）", ")").replace(" (", "(")


def _language_header(text: str) -> tuple[str, str] | None:
    """Header such as 名称（en） or Name (en), in any supported language → (kind, language code)."""
    written = _parens(text)
    for kind, label in LANGUAGE_HEADERS.items():
        for variant in variants(label):
            pattern = re.escape(_parens(variant)).replace(re.escape("{lang}"), _LANGUAGE_CODE)
            found = re.fullmatch(pattern, written)
            if found:
                return kind, found.group(1)
    return None


def _language_title(kind: str, language: str) -> str:
    return tr(LANGUAGE_HEADERS[kind], lang=language)


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
    return bool(header & _written(N_("名称"))) and bool(header & _written(N_("字段 ID")))


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


def _languages(template: dict[str, Any]) -> list[str]:
    """Languages other than the primary one that have a text somewhere in the template, in order of
    first appearance."""
    groups: list[dict[str, str]] = [
        template.get("titles") or {},
        template.get("descriptions") or {},
    ]
    for f in template.get("fields", []):
        groups += [f.get("names") or {}, f.get("descriptions") or {}]
    for t in template.get("tables", []):
        groups += [t.get("names") or {}, t.get("descriptions") or {}]
        for c in t.get("columns", []):
            groups += [c.get("names") or {}, c.get("descriptions") or {}]
    primary = template.get("language") or "zh"
    found = dict.fromkeys(code for group in groups for code in group if code != primary)
    return list(found)


def to_excel(template: dict[str, Any]) -> bytes:
    """template: a TemplateFile dictionary (title, fields, tables, rules, entities, references,
    …)."""
    book = Workbook()
    info = book.worksheets[0]
    info.title = _sheet_title("info")
    _setup(info, INFO_HEADERS, [16, 60])
    separator = tr("、")
    languages = _languages(template)
    domains = template.get("domains") or []
    for key, value in (
        ("format", FORMAT),
        ("format_version", FORMAT_VERSION),
        ("title", template["title"]),
        # Known domains in the workbook's language (the interface language, like its headers).
        ("category", domain_label(template.get("category", ""), ui_language())),
        ("domains", separator.join(domain_label(d, ui_language()) for d in domains)),
        ("countries", separator.join(template.get("countries") or []) or tr(GENERIC)),
        ("tags", separator.join(template.get("tags") or [])),
        ("description", template.get("description", "")),
        ("language", template.get("language") or "zh"),
        ("version", template.get("version", 1)),
        (
            "hint",
            tr(
                "修改后，在“模板”页点击“导入模板”并选择此文件。按原表导出的版"
                "式不包含在此文件中，如需迁移请使用 JSON 导出。"
            ),
        ),
    ):
        info.append([tr(INFO[key]), value])
    for language in languages:
        info.append(
            [_language_title("title", language), (template.get("titles") or {}).get(language, "")]
        )
        info.append(
            [
                _language_title("description", language),
                (template.get("descriptions") or {}).get(language, ""),
            ]
        )

    extra = [
        _language_title(kind, language)
        for language in languages
        for kind in ("label", "description")
    ]
    fields_ws = book.create_sheet(_sheet_title("fields"))
    _setup(
        fields_ws,
        [*(tr(h) for h in FIELD_HEADERS), *extra],
        [16, 18, 8, 6, 9, 6, 6, 24, 24, 50, 28, 8, 12, 8] + [24] * len(extra),
        translate=False,
    )
    for f in template["fields"]:
        fields_ws.append([*_field_row(f), *_language_row(f, languages)])
    _choices(fields_ws, len(template["fields"]) + 200, 0)

    table_extra = [
        _language_title(kind, language)
        for language in languages
        for kind in ("table_label", "table_description")
    ]
    tables_ws = book.create_sheet(_sheet_title("tables"))
    _setup(
        tables_ws,
        [*(tr(h) for h in TABLE_HEADERS), *table_extra, *extra],
        [12, 14, 30, 9, 10, 16, 18, 8, 6, 9, 6, 6, 24, 24, 50, 28, 8, 12, 8]
        + [24] * (len(table_extra) + len(extra)),
        translate=False,
    )
    for t in template.get("tables", []):
        table_texts = [
            (t.get(group) or {}).get(language, "")
            for language in languages
            for group in ("names", "descriptions")
        ]
        for c in t["columns"]:
            tables_ws.append(
                [
                    t["name"],
                    t["label"],
                    t.get("description", ""),
                    _yes(t.get("required")),
                    _yes(t.get("export", True)),
                    *_field_row(c),
                    *table_texts,
                    *_language_row(c, languages),
                ]
            )
    _choices(tables_ws, 500, 5)

    rules_ws = book.create_sheet(_sheet_title("rules"))
    _setup(rules_ws, RULE_HEADERS, [22, 46, 8, 6, 46, 16])
    chosen = set(template.get("rules", []))
    # List only the rules this template selected (a lab report does not list invoice, receipt or air
    # ticket rules). To add a rule, enter its id in an empty row below and set "use" to "yes"; it is
    # validated on import. Custom rules follow, with a condition instead of an id.
    by_id = {r.id: r for r in ALL_RULES}
    listed = [by_id[i] for i in dict.fromkeys(template.get("rules", [])) if i in by_id]
    for rule in listed:
        rules_ws.append(
            [
                rule.id,
                tr(rule.title),
                tr(SEVERITIES[rule.severity]),
                _yes(rule.id in chosen),
                "",
                "",
            ]
        )
    custom = template.get("custom_rules") or []
    for c in custom:
        rules_ws.append(
            [
                "",
                c.get("title", ""),
                tr(SEVERITIES.get(c.get("severity", "error"), SEVERITIES["error"])),
                _yes(True),
                c.get("check", ""),
                c.get("per_row") or "",
            ]
        )
    # Leave a few empty rows for additions before import.
    _yes_no(rules_ws, "D", max(len(listed) + len(custom), 30) + 1)
    _severity_choices(rules_ws, max(len(listed) + len(custom), 30) + 1)

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
    date_range = f.get("type") == "date_range"
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
        f.get("compute") or "",
        _yes(f.get("unique")),
        # Date range settings only mean something for date range fields.
        tr(RANGE_EXPORTS.get(f.get("range_export") or "normalized", RANGE_EXPORTS["normalized"]))
        if date_range
        else "",
        (f.get("range_separator") or DEFAULT_SEPARATOR) if date_range else "",
    ]


def _language_row(f: dict[str, Any], languages: list[str]) -> list[str]:
    """Name and description of a field in each other language (empty when not written)."""
    return [
        (f.get(group) or {}).get(language, "")
        for language in languages
        for group in ("names", "descriptions")
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
    for col in (4, 5, 6, 7, 12):
        _yes_no(ws, get_column_letter(offset + col), rows)
    ranges = DataValidation(
        type="list",
        formula1=f'"{",".join(tr(r) for r in RANGE_EXPORTS.values())}"',
        allow_blank=True,
    )
    ws.add_data_validation(ranges)
    ranges.add(f"{get_column_letter(offset + 13)}2:{get_column_letter(offset + 13)}{rows}")
    if offset:
        for col in (4, 5):
            _yes_no(ws, get_column_letter(col), rows)


def _severity_choices(ws: Worksheet, rows: int) -> None:
    choice = DataValidation(
        type="list",
        formula1=f'"{",".join(tr(v) for v in SEVERITIES.values())}"',
        allow_blank=True,
    )
    ws.add_data_validation(choice)
    choice.add(f"C2:C{rows}")


def _yes_no(ws: Worksheet, letter: str, rows: int) -> None:
    choice = DataValidation(type="list", formula1=f'"{tr(YES)},{tr(NO)}"', allow_blank=True)
    ws.add_data_validation(choice)
    choice.add(f"{letter}2:{letter}{rows}")


# ---------- Excel → template ----------


@dataclass
class Read:
    """A standard template workbook read back: the TemplateFile dictionary and what was skipped."""

    template: dict[str, Any]
    ignored: list[str] = field(default_factory=list)
    """Notes on columns and template rows that were not recognized (and therefore not imported)."""


def from_excel(data: bytes) -> Read:
    """Read a standard template workbook. Errors name the sheet and row.

    Workbooks of any layout version up to FORMAT_VERSION are read; a newer version is refused.
    Columns and template rows that are missing (removed by hand, or added in a later version) take
    their defaults. Columns that are not recognized (a typo, a column added by hand) are skipped
    and listed in Read.ignored."""
    book = load_workbook(io.BytesIO(data), data_only=True)
    read = Read(template={})
    names = _canonical(list(INFO.values()))
    info: dict[str, Any] = {}
    unknown_rows: list[str] = []
    for r in _rows(book, "info")[1:]:  # Below the header row.
        if not r or not _str(r[0]):
            continue
        label = _str(r[0])
        value = r[1] if len(r) > 1 else None
        if label in names:
            info[names[label]] = value
        elif (found := _language_header(label)) is not None:
            info[f"{found[0]}@{found[1]}"] = value
        elif not _retired(label):
            unknown_rows.append(label)
    if unknown_rows:
        read.ignored.append(
            tr(
                "「{sheet}」表中无法识别的项目已忽略：{names}",
                sheet=_sheet_title("info"),
                names=tr("、").join(unknown_rows),
            )
        )
    version = _version(info.get(INFO["format_version"]))
    if version > FORMAT_VERSION:
        raise StandardError(
            tr(
                "此文件由更新版本的 JacarandaParse 导出（格式版本 {found}），"
                "当前版本最高支持格式版本 {supported}，请升级后再导入",
                found=version,
                supported=FORMAT_VERSION,
            )
        )
    title = str(info.get(INFO["title"]) or "").strip()
    if not title:
        raise StandardError(tr("“模板”表中未填写模板名称"))

    fields_sheet = tr("字段表")
    fields = []
    for counter, (n, row) in enumerate(
        _numbered(book, "fields", FIELD_HEADERS, read.ignored), start=1
    ):
        fields.append(_field(row, fields_sheet, n, f"field_{counter}"))

    tables_sheet = tr("表格表")
    tables: dict[str, dict[str, Any]] = {}
    for n, row in _numbered(book, "tables", TABLE_HEADERS, read.ignored):
        table_id = _str(row.get(N_("表格 ID")))
        if not table_id:
            raise StandardError(tr("表格表第 {n} 行：未填写表 ID", n=n))
        table = tables.setdefault(
            table_id,
            {
                "name": table_id,
                "label": _str(row.get(N_("表格名称"))) or table_id,
                "description": _str(row.get(N_("表格说明"))),
                "required": _bool(row.get(N_("至少一行")), False, tables_sheet, n),
                "export": _bool(row.get(N_("导出整张表格")), True, tables_sheet, n),
                "names": _texts(row, "table_label"),
                "descriptions": _texts(row, "table_description"),
                "columns": [],
            },
        )
        table["columns"].append(_field(row, tables_sheet, n, f"col_{len(table['columns']) + 1}"))

    rules_sheet = tr("规则表")
    rules: list[str] = []
    custom_rules: list[dict[str, Any]] = []
    for n, row in _numbered(book, "rules", RULE_HEADERS, read.ignored):
        check = _str(row.get(N_("条件")))
        if check:
            # Custom rules are listed because the template has them; "use" = no drops one.
            if _bool(row.get(N_("启用")), True, rules_sheet, n):
                custom_rules.append(
                    {
                        "title": _str(row.get(N_("名称"))),
                        "check": check,
                        "severity": _severity(row.get(N_("级别")), rules_sheet, n),
                        "per_row": _str(row.get(N_("逐行检查的表格"))) or None,
                    }
                )
        elif _bool(row.get(N_("启用")), False, rules_sheet, n):
            rules.append(_str(row.get(N_("编号"))))
    entities = [
        {
            "name": _str(row.get(N_("对象 ID"))),
            "label": _str(row.get(N_("名称"))),
            "key": _str(row.get(N_("编号字段"))),
            "fields": _list(row.get(N_("包含字段"))),
        }
        for _, row in _numbered(book, "entities", ENTITY_HEADERS, read.ignored)
    ]
    references = []
    for n, row in _numbered(book, "references", REFERENCE_HEADERS, read.ignored):
        sheet = _str(row.get(N_("数据所在 sheet")))
        if sheet not in book.sheetnames:
            raise StandardError(
                tr("参照表第 {n} 行：未找到数据所在的 sheet「{sheet}」", n=n, sheet=sheet)
            )
        values = list(book[sheet].iter_rows(values_only=True))
        columns = [_str(v) for v in values[0] if _str(v)] if values else []
        references.append(
            {
                "name": _str(row.get(N_("参照表 ID"))),
                "label": _str(row.get(N_("名称"))),
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
    read.template = {
        "format": FORMAT,
        "title": title,
        "category": _str(info.get(INFO["category"])),
        # Without these rows the domain comes from "type" and the country from the selected rules.
        **({"domains": _list(info.get(INFO["domains"]))} if INFO["domains"] in info else {}),
        **(
            {"countries": [] if countries_text in generic else _list(countries_text)}
            if INFO["countries"] in info
            else {}
        ),
        **({"tags": _list(info.get(INFO["tags"]))} if INFO["tags"] in info else {}),
        "description": _str(info.get(INFO["description"])),
        # Without a primary language row the template is in the default language.
        "language": _str(info.get(INFO["language"])) or "zh",
        "titles": _texts(info, "title"),
        "descriptions": _texts(info, "description"),
        "version": int(info.get(INFO["version"]) or 1),
        "fields": fields,
        "tables": list(tables.values()),
        "rules": rules,
        "custom_rules": custom_rules,
        "entities": entities,
        "references": references,
    }
    return read


def _version(value: Any) -> int:
    """Layout version of a workbook; workbooks without one are layout 1."""
    try:
        return int(_str(value) or 1)
    except ValueError:
        return 1


def _retired(text: str) -> bool:
    return any(text in _written(label) for label in RETIRED)


def _texts(row: dict[str, Any], kind: str) -> dict[str, str]:
    """Texts in other languages read from language columns ("kind@code" keys)."""
    prefix = f"{kind}@"
    return {
        key[len(prefix) :]: _str(value)
        for key, value in row.items()
        if isinstance(key, str) and key.startswith(prefix) and _str(value)
    }


def _severity(value: Any, where: str, n: int) -> str:
    text = _str(value)
    if not text:
        return "error"
    for key, label in SEVERITIES.items():
        if text == key or text in variants(label):
            return key
    raise StandardError(
        tr(
            "{sheet}第 {n} 行：级别只能为 {values}",
            sheet=where,
            n=n,
            values=tr("、").join(tr(v) for v in SEVERITIES.values()),
        )
    )


def _rows(book: Any, key: str) -> list[tuple[Any, ...]]:
    name = _sheet(book, key)
    if name is None:
        return []
    return list(book[name].iter_rows(values_only=True))


REQUIRED_COLUMNS = {"fields": [N_("名称")], "tables": [N_("表格 ID"), N_("名称")]}
"""Columns a sheet must have (message ids)."""


def _numbered(
    book: Any, key: str, headers: list[str], ignored: list[str]
) -> list[tuple[int, dict[str, Any]]]:
    """Data rows of a fixed sheet, keyed by header message id (columns in another language by
    "kind@code"), with their row numbers. Unrecognized columns are noted in ignored."""
    rows = _rows(book, key)
    if not rows:
        return []
    names = _canonical(headers)
    header: list[str] = []
    unknown: list[str] = []
    for cell in rows[0]:
        text = _str(cell)
        if text in names:
            header.append(names[text])
        elif (found := _language_header(text)) is not None:
            header.append(f"{found[0]}@{found[1]}")
        else:
            header.append(text)
            if text and not _retired(text):
                unknown.append(text)
    missing = [h for h in REQUIRED_COLUMNS.get(key, []) if h not in header]
    if missing:
        raise StandardError(
            tr(
                "“{sheet}”表缺少以下列：{columns}",
                sheet=_sheet_title(key),
                columns=tr("、").join(tr(h) for h in missing),
            )
        )
    if unknown:
        ignored.append(
            tr(
                "「{sheet}」表中无法识别的列已忽略：{columns}",
                sheet=_sheet_title(key),
                columns=tr("、").join(unknown),
            )
        )
    result = []
    for n, values in enumerate(rows[1:], start=2):
        if not any(_str(v) for v in values):
            continue
        result.append((n, dict(zip(header, values, strict=False))))
    return result


def _field(row: dict[str, Any], where: str, n: int, default_id: str) -> dict[str, Any]:
    label = _str(row.get(N_("名称")))
    if not label:
        raise StandardError(tr("{sheet}第 {n} 行：未填写名称", sheet=where, n=n))
    written = _str(row.get(N_("类型")))
    kind = _type_back(written) if written else "string"
    if kind is None:
        raise StandardError(
            tr(
                "{sheet}第 {n} 行：类型只能为 {types}",
                sheet=where,
                n=n,
                types=tr("、").join(tr(t) for t in TYPES.values()),
            )
        )
    sensitive = _bool(row.get(N_("敏感")), False, where, n)
    return {
        "name": _str(row.get(N_("字段 ID"))) or default_id,
        "label": label,
        "type": kind,
        "required": _bool(row.get(N_("必填")), False, where, n),
        "extract": _bool(row.get(N_("从单据抽取")), True, where, n),
        "export": _bool(row.get(N_("导出")), not sensitive, where, n),
        "sensitive": sensitive,
        "options": _list(row.get(N_("可选值"))),
        "aliases": _list(row.get(N_("别名"))),
        "description": _str(row.get(N_("说明"))),
        "compute": _str(row.get(N_("公式"))),
        "unique": _bool(row.get(N_("不能重复")), False, where, n),
        **_range(row, kind, where, n),
        "names": _texts(row, "label"),
        "descriptions": _texts(row, "description"),
    }


def _range(row: dict[str, Any], kind: str, where: str, n: int) -> dict[str, Any]:
    """Date range export settings; only date range fields have them."""
    if kind != "date_range":
        return {}
    text = _str(row.get(N_("日期范围导出")))
    export = "normalized"
    if text:
        found = next(
            (k for k, label in RANGE_EXPORTS.items() if text == k or text in variants(label)),
            None,
        )
        if found is None:
            raise StandardError(
                tr(
                    "{sheet}第 {n} 行：日期范围导出只能为 {values}",
                    sheet=where,
                    n=n,
                    values=tr("、").join(tr(v) for v in RANGE_EXPORTS.values()),
                )
            )
        export = found
    # The separator keeps its spaces (" ~ "), so it is read without trimming.
    raw = row.get(N_("分隔符"))
    separator = str(raw) if raw is not None and str(raw) != "" else DEFAULT_SEPARATOR
    return {"range_export": export, "range_separator": separator}


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
            ("{sheet}第 {n} 行：是/否单元格填写了「{value}」，只能为 {yes} 或 {no}"),
            sheet=where,
            n=n,
            value=value,
            yes=tr(YES),
            no=tr(NO),
        )
    )
