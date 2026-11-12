"""按预览里确认的结果，把业务 Excel 变成模板定义（ADR 0031）。

输入是每张 sheet 的角色和每一列的来源（预览里人确认、改过的），输出是模板定义：
- 单据表、对象表的列，明细表里"单据上的字段" → 普通字段（同一个字段 ID 只建一次）
- 明细表自己的列 → 表格
- 对象表 → 对象（编号字段 + 属于它的字段）
- 参照表 → 参照名单（整张表的数据存进模板）
- 每张 sheet 每一列填什么 → 工作簿样式（导出时填回原 Excel）
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from jacara.templates import load_starter
from jacara.templates.extras import MAX_REFERENCE_ROWS, normalize
from jacara.workbook.analyze import _cells, _headers, _text, data_rows, open_workbook, slug


@dataclass
class ColumnChoice:
    index: int
    header: str
    source: str
    field: str = ""
    label: str = ""
    type: str = "string"
    required: bool = False
    sensitive: bool = False
    description: str = ""
    aliases: list[str] = dc_field(default_factory=list)
    options: list[str] = dc_field(default_factory=list)
    auto: str = ""
    lookup: str = ""
    """「参照表 sheet 名::列名」。"""


@dataclass
class SheetChoice:
    name: str
    role: str
    header_row: int
    columns: list[ColumnChoice]
    same_as: str = ""
    key_header: str = ""
    match_field: str = ""
    """参照表：「sheet 名::表头」，拿那一列的值来对照。"""


class BuildError(ValueError):
    pass


def _ids(sheets: list[SheetChoice], role: str, prefix: str) -> dict[str, str]:
    """每张 sheet 一个英文名（表格、对象、参照表用）：英文 sheet 名转一下，中文的编号。"""
    result: dict[str, str] = {}
    for n, s in enumerate((s for s in sheets if s.role == role), start=1):
        candidate = slug(s.name) or (prefix if n == 1 else f"{prefix}_{n}")
        while candidate in result.values():
            candidate = f"{candidate}_{n}"
        result[s.name] = candidate
    return result


def _field(c: ColumnChoice) -> dict[str, Any]:
    return {
        "name": c.field,
        "label": c.label.strip() or c.header,
        "type": c.type,
        "required": c.required,
        "description": c.description,
        "aliases": [a for a in c.aliases if normalize(a) != normalize(c.label or c.header)],
        "export": not c.sensitive,
        "sensitive": c.sensitive,
        "options": c.options,
        "extract": c.source != "manual",
    }


def build_definition(
    data: bytes,
    filename: str,
    file_key: str,
    sheets: list[SheetChoice],
    starter: str = "",
) -> dict[str, Any]:
    """返回模板定义（fields、tables、entities、references、rules、workbook）。"""
    active = [s for s in sheets if s.role != "ignore"]
    tables = _ids(active, "items", "items")
    entities = _ids(active, "entity", "entity")
    references = _ids(active, "reference", "ref")

    fields: dict[str, dict[str, Any]] = {}
    table_defs: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    """「sheet 名::表头」→ 这一格填什么（字段 ID 或 items.amount）。"""

    def add_field(c: ColumnChoice) -> str:
        if not c.field:
            raise BuildError(f"「{c.header}」还没有字段 ID")
        existing = fields.get(c.field)
        if existing is None:
            fields[c.field] = _field(c)
        else:
            existing["options"] = list(dict.fromkeys([*existing["options"], *c.options]))
            existing["required"] = existing["required"] or c.required
        return c.field

    by_name = {s.name: s for s in active}
    for s in active:
        if s.same_as:
            continue
        for c in s.columns:
            key = f"{s.name}::{c.header}"
            if (c.source in ("extract", "manual") and s.role in ("document", "entity")) or (
                c.source == "doc" and s.role == "items"
            ):
                refs[key] = add_field(c)
            elif c.source in ("extract", "manual") and s.role == "items":
                table = table_defs.setdefault(
                    tables[s.name],
                    {
                        "name": tables[s.name],
                        "label": s.name,
                        "description": f"「{s.name}」里的每一行",
                        "required": False,
                        "export": True,
                        "columns": [],
                    },
                )
                if not c.field:
                    raise BuildError(f"「{s.name}」的「{c.header}」还没有列 ID")
                if any(col["name"] == c.field for col in table["columns"]):
                    raise BuildError(f"「{s.name}」里列 ID {c.field} 重复了")
                table["columns"].append(_field(c))
                refs[key] = f"{tables[s.name]}.{c.field}"

    entity_defs = []
    for s in active:
        if s.role != "entity":
            continue
        key_column = next((c for c in s.columns if c.header == s.key_header), None)
        if key_column is None or refs.get(f"{s.name}::{key_column.header}") is None:
            raise BuildError(f"对象表「{s.name}」要选一列当编号（从单据抽或人工填的列）")
        members = [
            refs[f"{s.name}::{c.header}"] for c in s.columns if f"{s.name}::{c.header}" in refs
        ]
        entity_defs.append(
            {
                "name": entities[s.name],
                "label": s.name,
                "key": refs[f"{s.name}::{key_column.header}"],
                "fields": members,
            }
        )

    book = open_workbook(data)
    reference_defs = []
    for s in active:
        if s.role != "reference":
            continue
        ws = book[s.name]
        rows = _cells(ws)
        headers = _headers(ws, rows, s.header_row)
        body = data_rows(rows, s.header_row)[:MAX_REFERENCE_ROWS]
        columns = [h for _, h in headers]
        values = [
            [_text(r[col - 1]) if col - 1 < len(r) else "" for col, _ in headers] for r in body
        ]
        match_field = refs.get(s.match_field, "") if s.match_field else ""
        if s.match_field and not match_field:
            raise BuildError(
                f"参照表「{s.name}」要对照的列「{s.match_field.split('::')[-1]}」没有建成字段"
            )
        reference_defs.append(
            {
                "name": references[s.name],
                "label": s.name,
                "columns": columns,
                "rows": values,
                "match_field": match_field,
                "match_column": s.key_header if match_field else "",
            }
        )

    def column_ref(s: SheetChoice, c: ColumnChoice) -> str:
        origin = by_name.get(s.same_as) if s.same_as else s
        if origin is None:
            return ""
        if c.source == "auto":
            return f"auto:{c.auto}" if c.auto else ""
        if c.source == "lookup" and "::" in c.lookup:
            sheet, _, column = c.lookup.partition("::")
            return f"ref:{references[sheet]}.{column}" if sheet in references else ""
        if s.same_as:
            match = next(
                (o for o in origin.columns if normalize(o.header) == normalize(c.header)), None
            )
            return column_ref(origin, match) if match else ""
        return refs.get(f"{s.name}::{c.header}", "")

    workbook = {
        "file": file_key,
        "filename": filename,
        "sheets": [
            {
                "name": s.name,
                "role": s.role,
                "header_row": max(1, s.header_row),
                "table": tables.get(s.name, ""),
                "entity": entities.get(s.name, ""),
                "reference": references.get(s.name, ""),
                "same_as": s.same_as,
                "columns": [
                    {"index": c.index, "header": c.header, "ref": column_ref(s, c)}
                    for c in s.columns
                ]
                if s.role in ("document", "items", "entity")
                else [],
            }
            for s in sheets
        ],
    }

    definition: dict[str, Any] = {
        "fields": list(fields.values()),
        "tables": list(table_defs.values()),
        "entities": entity_defs,
        "references": reference_defs,
        "rules": _starter_rules(starter, fields, table_defs),
        "workbook": workbook,
    }
    if not definition["fields"]:
        raise BuildError(
            "没有任何字段：至少要有一张单据表或对象表，或者明细表里标一列“单据上的字段”"
        )
    return definition


def _starter_rules(
    starter: str, fields: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> list[str]:
    """对上了起步模板：它的规则里，要用的字段这里都有的，一起带过来。"""
    if not starter:
        return []
    names = set(fields)
    names |= {f"{t}.*.{c['name']}" for t, d in tables.items() for c in d["columns"]}
    table_columns = {t: {c["name"] for c in d["columns"]} for t, d in tables.items()}
    rules = []
    for rule in load_starter(starter).rules:
        if rule.per_row:
            ok = all(n in table_columns.get(rule.per_row, set()) for n in rule.needs)
        else:
            ok = all(n in names for n in rule.needs)
        if ok:
            rules.append(rule.id)
    return rules
