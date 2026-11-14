"""Turn a business workbook into a template definition according to the confirmed preview.

Input: the role of each sheet and the source of each column (confirmed or corrected in the
preview). Output: a template definition:
- Columns of document and entity sheets, and document-level fields in line-item sheets → regular
  fields (each field ID created once)
- Columns specific to line-item sheets → tables
- Entity sheets → subjects (identifier field + its fields)
- Reference sheets → reference lists (the whole sheet's data stored in the template)
- Content of each column of each sheet → workbook layout (for exporting into the original
  workbook)"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from jacara.core.i18n import tr
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
    """"reference sheet name::column name"."""


@dataclass
class SheetChoice:
    name: str
    role: str
    header_row: int
    columns: list[ColumnChoice]
    same_as: str = ""
    key_header: str = ""
    match_field: str = ""
    """Reference sheets: "sheet name::header" of the column whose values are matched."""


class BuildError(ValueError):
    pass


def _ids(sheets: list[SheetChoice], role: str, prefix: str) -> dict[str, str]:
    """An English name per sheet (for tables, subjects and reference tables): English sheet names
    converted, others numbered."""
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
    """Return the template definition (fields, tables, entities, references, rules, workbook)."""
    active = [s for s in sheets if s.role != "ignore"]
    tables = _ids(active, "items", "items")
    entities = _ids(active, "entity", "entity")
    references = _ids(active, "reference", "ref")

    fields: dict[str, dict[str, Any]] = {}
    table_defs: dict[str, dict[str, Any]] = {}
    refs: dict[str, str] = {}
    """"sheet name::header" → content of the cell (field ID or items.amount)."""

    def add_field(c: ColumnChoice) -> str:
        if not c.field:
            raise BuildError(tr("「{header}」还没有字段 ID", header=c.header))
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
                        "description": tr("「{name}」里的每一行", name=s.name),
                        "required": False,
                        "export": True,
                        "columns": [],
                    },
                )
                if not c.field:
                    raise BuildError(
                        tr("「{name}」的「{header}」还没有列 ID", name=s.name, header=c.header)
                    )
                if any(col["name"] == c.field for col in table["columns"]):
                    raise BuildError(
                        tr("「{name}」里列 ID {field} 重复了", name=s.name, field=c.field)
                    )
                table["columns"].append(_field(c))
                refs[key] = f"{tables[s.name]}.{c.field}"

    entity_defs = []
    for s in active:
        if s.role != "entity":
            continue
        key_column = next((c for c in s.columns if c.header == s.key_header), None)
        if key_column is None or refs.get(f"{s.name}::{key_column.header}") is None:
            raise BuildError(
                tr("对象表「{name}」要选一列当编号（从单据抽或人工填的列）", name=s.name)
            )
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
                tr(
                    "参照表「{name}」要对照的列「{column}」没有建成字段",
                    name=s.name,
                    column=s.match_field.split("::")[-1],
                )
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
            tr("没有任何字段：至少要有一张单据表或对象表，或者明细表里标一列“单据上的字段”")
        )
    return definition


def _starter_rules(
    starter: str, fields: dict[str, Any], tables: dict[str, dict[str, Any]]
) -> list[str]:
    """Matched a starter template: carry over its rules whose fields are all present here."""
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
