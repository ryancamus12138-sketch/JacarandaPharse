"""模板里和"多张表"有关的部分（ADR 0031）：对象、参照表、工作簿样式。

一个 Excel 里的每张 sheet 归成五种角色之一：
- document 单据表：一份单据一行 → 模板的普通字段
- items    明细表：一份单据多行 → 模板的表格（tables）
- entity   对象表：跨单据的同一个对象（人、公司、地点）一行，按编号合并 → entities
- reference 参照表：固定资料，导入一次，用来对照、补编号 → references
- options / ignore：说明、下拉选项（选项变成字段的可选值）、不用的 sheet

工作簿样式（workbook）记下原 Excel 每张 sheet 的角色、表头在第几行、每一列填什么，
导出时把数据填回原 Excel（原文件存在对象存储里，file 是它的位置）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, get_args

_ID = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_REFERENCE_ROWS = 5000
"""一张参照表最多存多少行（存在模板定义里，太大会拖慢每次读模板）。"""

SheetRole = Literal["document", "items", "entity", "reference", "options", "ignore"]
ROLE_NAMES: dict[str, str] = {
    "document": "单据表",
    "items": "明细表",
    "entity": "对象表",
    "reference": "参照表",
    "options": "说明 / 选项",
    "ignore": "不用",
}

AUTO_COLUMNS: dict[str, str] = {
    "seq": "序号",
    "filename": "文件名",
    "page": "第几页",
    "row": "明细第几行",
    "uploaded_at": "上传时间",
    "processed_at": "处理时间",
    "reviewed_at": "审核时间",
}
"""系统自动填的列：auto:seq 这样写。"""


class ExtrasError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateEntity:
    """对象：几份单据可能说的是同一个（同一个病人、同一个供应商），按编号合并成一行。"""

    name: str
    label: str
    key: str
    """编号字段（模板里的一个普通字段），编号相同就是同一个对象。"""
    fields: tuple[str, ...]
    """属于这个对象的普通字段（包括编号字段）。"""


@dataclass(frozen=True)
class TemplateReference:
    """参照表：一份固定名单（实验室、科室、费用类别……），不从单据抽。

    match_field 有值时：拿单据上这个字段的值，到 match_column 这一列里找；
    找到了，这一行的其他列可以填进导出（ref:名单.列名），找不到就提醒。
    """

    name: str
    label: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    match_field: str = ""
    match_column: str = ""

    def find(self, value: object) -> dict[str, str] | None:
        """按 match_column 找一行（忽略大小写、全角半角、空格）。找不到返回 None。"""
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
    """第几列，从 1 开始（A 列是 1）。"""
    header: str
    ref: str = ""
    """这一列填什么：字段 ID、表格的列（items.amount）、auto:seq、ref:名单.列名；空 = 不填。"""


@dataclass(frozen=True)
class WorkbookSheet:
    name: str
    role: SheetRole
    header_row: int
    """表头在第几行（从 1 开始）。两层表头时是下面那一行。"""
    columns: tuple[WorkbookColumn, ...] = ()
    table: str = ""
    """明细表对应模板里的哪张表格。"""
    entity: str = ""
    """对象表对应哪个对象。"""
    reference: str = ""
    """参照表对应哪份名单。"""
    same_as: str = ""
    """和哪张 sheet 一样（例如按月分的 1 月、2 月）：导出时只填第一张的副本。"""


@dataclass(frozen=True)
class Workbook:
    file: str
    """原 Excel 在对象存储里的位置。"""
    filename: str
    sheets: tuple[WorkbookSheet, ...]


def normalize(text: str) -> str:
    """对照用：全角变半角、去掉空格、不分大小写。"""
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def parse_entities(raw: Any, fields: set[str], where: str) -> tuple[TemplateEntity, ...]:
    entities: list[TemplateEntity] = []
    for i, item in enumerate(raw or [], start=1):
        name = item.get("name")
        if not isinstance(name, str) or not _ID.match(name):
            raise ExtrasError(f"{where}：第 {i} 个对象的 name 只能用小写英文字母、数字和下划线")
        members = tuple(item.get("fields") or [])
        key = item.get("key")
        if key not in fields:
            raise ExtrasError(f"{where}：对象 {name} 的编号字段 {key} 不在模板的字段里")
        missing = [f for f in members if f not in fields]
        if missing:
            raise ExtrasError(f"{where}：对象 {name} 的字段 {'、'.join(missing)} 不在模板的字段里")
        if any(e.name == name for e in entities):
            raise ExtrasError(f"{where}：对象 {name} 重复了")
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
    """fields：能拿来对照的字段（普通字段的 ID，或者表格的列 items.lab_name）。"""
    references: list[TemplateReference] = []
    for i, item in enumerate(raw or [], start=1):
        name = item.get("name")
        if not isinstance(name, str) or not _ID.match(name):
            raise ExtrasError(f"{where}：第 {i} 份参照表的 name 只能用小写英文字母、数字和下划线")
        columns = tuple(str(c) for c in item.get("columns") or [])
        if not columns or len(set(columns)) != len(columns):
            raise ExtrasError(f"{where}：参照表 {name} 的列名不能为空、不能重复")
        rows = tuple(tuple("" if v is None else str(v) for v in r) for r in item.get("rows") or [])
        if len(rows) > MAX_REFERENCE_ROWS:
            raise ExtrasError(f"{where}：参照表 {name} 超过 {MAX_REFERENCE_ROWS} 行")
        match_field = str(item.get("match_field") or "")
        match_column = str(item.get("match_column") or "")
        if match_field and match_field not in fields:
            raise ExtrasError(f"{where}：参照表 {name} 要对照的字段 {match_field} 不在模板里")
        if match_column and match_column not in columns:
            raise ExtrasError(f"{where}：参照表 {name} 没有叫 {match_column} 的列")
        if bool(match_field) != bool(match_column):
            raise ExtrasError(f"{where}：参照表 {name} 的对照字段和对照列要一起写")
        if any(r.name == name for r in references):
            raise ExtrasError(f"{where}：参照表 {name} 重复了")
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
    """refs：这个模板里能填进格子的东西（字段 ID、items.amount）。"""
    if not raw:
        return None
    sheets: list[WorkbookSheet] = []
    for item in raw.get("sheets") or []:
        name = str(item.get("name") or "")
        role = item.get("role")
        if role not in get_args(SheetRole):
            raise ExtrasError(f"{where}：sheet「{name}」的角色 {role} 不认识")
        header_row = item.get("header_row")
        if not isinstance(header_row, int) or header_row < 1:
            raise ExtrasError(f"{where}：sheet「{name}」的表头行要是正整数")
        columns: list[WorkbookColumn] = []
        for c in item.get("columns") or []:
            ref = str(c.get("ref") or "")
            if ref and not _known_ref(ref, refs, references):
                raise ExtrasError(
                    f"{where}：sheet「{name}」的列「{c.get('header')}」要填的 {ref} 不认识"
                )
            columns.append(
                WorkbookColumn(int(c.get("index") or 0), str(c.get("header") or ""), ref)
            )
        if any(c.index < 1 for c in columns):
            raise ExtrasError(f"{where}：sheet「{name}」有列的位置不对")
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
            raise ExtrasError(f"{where}：明细表「{name}」对应的表格 {sheet.table} 不在模板里")
        if role == "entity" and sheet.entity not in entities:
            raise ExtrasError(f"{where}：对象表「{name}」对应的对象 {sheet.entity} 不在模板里")
        if role == "reference" and sheet.reference not in references:
            raise ExtrasError(f"{where}：参照表「{name}」对应的名单 {sheet.reference} 不在模板里")
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
