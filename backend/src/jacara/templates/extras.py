"""Template parts related to multiple sheets: subjects, reference tables, workbook layout.

Each sheet of a workbook takes one of five roles:
- document: one row per document → regular template fields
- items: several rows per document → template tables
- entity: one row per subject shared across documents (person, company, place), merged by
  identifier → entities
- reference: fixed data imported once, used for matching and filling identifiers → references
- options / ignore: notes, drop-down options (options become a field's allowed values), unused
  sheets

The workbook layout records the role of each sheet of the original workbook, the header row and
the content of each column, so exports can fill data back into the original workbook (stored in
object storage; file is its key)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, get_args

from jacara.core.i18n import N_, join_items, stored

_ID = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_REFERENCE_ROWS = 5000
"""Maximum rows stored per reference table (stored in the template definition; large tables slow
down every template load)."""

SheetRole = Literal["document", "items", "entity", "reference", "options", "ignore"]
ROLE_NAMES: dict[str, str] = {
    "document": N_("单据表"),
    "items": N_("明细表"),
    "entity": N_("对象表"),
    "reference": N_("参照表"),
    "options": N_("说明 / 选项"),
    "ignore": N_("不用"),
}

AUTO_COLUMNS: dict[str, str] = {
    "seq": N_("序号"),
    "filename": N_("文件名"),
    "page": N_("第几页"),
    "row": N_("明细第几行"),
    "uploaded_at": N_("上传时间"),
    "processed_at": N_("处理时间"),
    "reviewed_at": N_("审核时间"),
}
"""Columns filled automatically by the system, written as auto:seq."""


class ExtrasError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateEntity:
    """Subject: several documents may refer to the same one (same patient, same supplier); merged
    into one row by identifier."""

    name: str
    label: str
    key: str
    """Identifier field (a regular template field); equal identifiers denote the same subject."""
    fields: tuple[str, ...]
    """Regular fields belonging to this subject (including the identifier field)."""


@dataclass(frozen=True)
class TemplateReference:
    """Reference table: a fixed list (laboratories, departments, expense categories, …), not
    extracted from documents.

        When match_field is set, the document's value of that field is looked up in column
    match_column; on a match, other columns of the row can fill export columns (ref:list.column);
    otherwise a warning is raised."""

    name: str
    label: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    match_field: str = ""
    match_column: str = ""

    def find(self, value: object) -> dict[str, str] | None:
        """Find a row by match_column (ignoring case, full-width/half-width and whitespace). None if
        not found."""
        if not self.match_column or value is None:
            return None
        index = self.columns.index(self.match_column)
        wanted = normalize(str(value))
        for row in self.rows:
            if index < len(row) and normalize(row[index]) == wanted:
                return dict(zip(self.columns, row, strict=False))
        return None

    def values(self) -> frozenset[str]:
        if not self.match_column:
            return frozenset()
        index = self.columns.index(self.match_column)
        return frozenset(normalize(r[index]) for r in self.rows if index < len(r) and r[index])


@dataclass(frozen=True)
class WorkbookColumn:
    index: int
    """Column number, 1-based (column A is 1)."""
    header: str
    ref: str = ""
    """Content of this column: a field ID, a table column (items.amount), auto:seq, ref:list.column;
    empty means not filled."""


@dataclass(frozen=True)
class WorkbookSheet:
    name: str
    role: SheetRole
    header_row: int
    """Header row (1-based). For two-level headers, the lower row."""
    columns: tuple[WorkbookColumn, ...] = ()
    table: str = ""
    """Template table corresponding to this line-item sheet."""
    entity: str = ""
    """Subject corresponding to this entity sheet."""
    reference: str = ""
    """Reference list corresponding to this reference sheet."""
    same_as: str = ""
    """Sheet with the same layout (for example monthly sheets January, February): exports fill only
    a copy of the first."""


@dataclass(frozen=True)
class Workbook:
    file: str
    """Object storage key of the original workbook."""
    filename: str
    sheets: tuple[WorkbookSheet, ...]


def normalize(text: str) -> str:
    """Matching key: full-width to half-width, whitespace removed, case-insensitive."""
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def parse_entities(raw: Any, fields: set[str], where: str) -> tuple[TemplateEntity, ...]:
    entities: list[TemplateEntity] = []
    for i, item in enumerate(raw or [], start=1):
        name = item.get("name")
        if not isinstance(name, str) or not _ID.match(name):
            raise ExtrasError(
                stored(
                    ("{where}：第 {i} 个对象的 name 只能包含小写英文字母、数字和下划线"),
                    where=where,
                    i=i,
                )
            )
        members = tuple(item.get("fields") or [])
        key = item.get("key")
        if key not in fields:
            raise ExtrasError(
                stored(
                    "{where}：对象 {name} 的编号字段 {key} 不是模板中的字段",
                    where=where,
                    name=name,
                    key=key,
                )
            )
        missing = [f for f in members if f not in fields]
        if missing:
            raise ExtrasError(
                stored(
                    "{where}：对象 {name} 的字段 {fields} 不是模板中的字段",
                    where=where,
                    name=name,
                    fields=join_items(missing),
                )
            )
        if any(e.name == name for e in entities):
            raise ExtrasError(stored("{where}：对象 {name} 重复", where=where, name=name))
        entities.append(
            TemplateEntity(
                name=name,
                label=str(item.get("label") or name),
                key=key,
                fields=members if key in members else (key, *members),
            )
        )
    return tuple(entities)


def parse_references(raw: Any, fields: set[str], where: str) -> tuple[TemplateReference, ...]:
    """fields: fields usable for matching (regular field IDs, or table columns such as
    items.lab_name)."""
    references: list[TemplateReference] = []
    for i, item in enumerate(raw or [], start=1):
        name = item.get("name")
        if not isinstance(name, str) or not _ID.match(name):
            raise ExtrasError(
                stored(
                    ("{where}：第 {i} 份参照表的 name 只能包含小写英文字母、数字和下划线"),
                    where=where,
                    i=i,
                )
            )
        columns = tuple(str(c) for c in item.get("columns") or [])
        if not columns or len(set(columns)) != len(columns):
            raise ExtrasError(
                stored("{where}：参照表 {name} 的列名不能为空或重复", where=where, name=name)
            )
        rows = tuple(tuple("" if v is None else str(v) for v in r) for r in item.get("rows") or [])
        if len(rows) > MAX_REFERENCE_ROWS:
            raise ExtrasError(
                stored(
                    "{where}：参照表 {name} 超过 {limit} 行",
                    where=where,
                    name=name,
                    limit=MAX_REFERENCE_ROWS,
                )
            )
        match_field = str(item.get("match_field") or "")
        match_column = str(item.get("match_column") or "")
        if match_field and match_field not in fields:
            raise ExtrasError(
                stored(
                    "{where}：参照表 {name} 的对照字段 {match_field} 不在模板中",
                    where=where,
                    name=name,
                    match_field=match_field,
                )
            )
        if match_column and match_column not in columns:
            raise ExtrasError(
                stored(
                    "{where}：参照表 {name} 中不存在名为 {match_column} 的列",
                    where=where,
                    name=name,
                    match_column=match_column,
                )
            )
        if bool(match_field) != bool(match_column):
            raise ExtrasError(
                stored(
                    "{where}：参照表 {name} 的对照字段与对照列需同时填写", where=where, name=name
                )
            )
        if any(r.name == name for r in references):
            raise ExtrasError(stored("{where}：参照表 {name} 重复", where=where, name=name))
        references.append(
            TemplateReference(
                name=name,
                label=str(item.get("label") or name),
                columns=columns,
                rows=rows,
                match_field=match_field,
                match_column=match_column,
            )
        )
    return tuple(references)


def parse_workbook(
    raw: Any,
    refs: set[str],
    tables: set[str],
    entities: set[str],
    references: dict[str, TemplateReference],
    where: str,
) -> Workbook | None:
    """refs: values that can fill cells in this template (field IDs, items.amount)."""
    if not raw:
        return None
    sheets: list[WorkbookSheet] = []
    for item in raw.get("sheets") or []:
        name = str(item.get("name") or "")
        role = item.get("role")
        if role not in get_args(SheetRole):
            raise ExtrasError(
                stored(
                    "{where}：sheet「{name}」的角色 {role} 无效",
                    where=where,
                    name=name,
                    role=role,
                )
            )
        header_row = item.get("header_row")
        if not isinstance(header_row, int) or header_row < 1:
            raise ExtrasError(
                stored("{where}：sheet「{name}」的表头行需为正整数", where=where, name=name)
            )
        columns: list[WorkbookColumn] = []
        for c in item.get("columns") or []:
            ref = str(c.get("ref") or "")
            if ref and not _known_ref(ref, refs, references):
                raise ExtrasError(
                    stored(
                        "{where}：sheet「{name}」中列「{header}」的填充内容 {ref} 无效",
                        where=where,
                        name=name,
                        header=c.get("header"),
                        ref=ref,
                    )
                )
            columns.append(
                WorkbookColumn(int(c.get("index") or 0), str(c.get("header") or ""), ref)
            )
        if any(c.index < 1 for c in columns):
            raise ExtrasError(
                stored("{where}：sheet「{name}」存在位置无效的列", where=where, name=name)
            )
        sheet = WorkbookSheet(
            name=name,
            role=role,
            header_row=header_row,
            columns=tuple(columns),
            table=str(item.get("table") or ""),
            entity=str(item.get("entity") or ""),
            reference=str(item.get("reference") or ""),
            same_as=str(item.get("same_as") or ""),
        )
        if role == "items" and sheet.table not in tables:
            raise ExtrasError(
                stored(
                    "{where}：明细表「{name}」对应的表格 {table} 不在模板中",
                    where=where,
                    name=name,
                    table=sheet.table,
                )
            )
        if role == "entity" and sheet.entity not in entities:
            raise ExtrasError(
                stored(
                    "{where}：对象表「{name}」对应的对象 {entity} 不在模板中",
                    where=where,
                    name=name,
                    entity=sheet.entity,
                )
            )
        if role == "reference" and sheet.reference not in references:
            raise ExtrasError(
                stored(
                    "{where}：参照表「{name}」对应的清单 {reference} 不在模板中",
                    where=where,
                    name=name,
                    reference=sheet.reference,
                )
            )
        sheets.append(sheet)
    return Workbook(
        file=str(raw.get("file") or ""),
        filename=str(raw.get("filename") or ""),
        sheets=tuple(sheets),
    )


def _known_ref(ref: str, refs: set[str], references: dict[str, TemplateReference]) -> bool:
    if ref.startswith("auto:"):
        return ref[5:] in AUTO_COLUMNS
    if ref.startswith("ref:"):
        name, _, column = ref[4:].partition(".")
        return name in references and column in references[name].columns
    return ref in refs


def entity_dict(e: TemplateEntity) -> dict[str, Any]:
    return {"name": e.name, "label": e.label, "key": e.key, "fields": list(e.fields)}


def reference_dict(r: TemplateReference) -> dict[str, Any]:
    return {
        "name": r.name,
        "label": r.label,
        "columns": list(r.columns),
        "rows": [list(row) for row in r.rows],
        "match_field": r.match_field,
        "match_column": r.match_column,
    }


def workbook_dict(w: Workbook) -> dict[str, Any]:
    return {
        "file": w.file,
        "filename": w.filename,
        "sheets": [
            {
                "name": s.name,
                "role": s.role,
                "header_row": s.header_row,
                "table": s.table,
                "entity": s.entity,
                "reference": s.reference,
                "same_as": s.same_as,
                "columns": [
                    {"index": c.index, "header": c.header, "ref": c.ref} for c in s.columns
                ],
            }
            for s in w.sheets
        ],
    }
