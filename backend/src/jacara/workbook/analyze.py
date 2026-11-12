"""分析一个业务 Excel：每张 sheet 的表头在哪、一行代表什么（角色）、每一列像什么（ADR 0031）。

只是"猜"：结果给人在预览里确认、修改，确认之后才建模板（build.py）。
规律：
- 表头行：前 20 行里第一行"文字格够多、都不像数字或日期"的；上面一行有横向合并的格子，就是两层表头。
- 数据行：表头下面到连着三行空行为止；"合计""总计"开头的行不算。
- 编号列：表头像编号（编号、号码、ID、No、代码），或者值都不重复。
- 角色：
  · 没有表头、只有一两列、或者名字像"说明""选项"的 → 说明 / 选项；被下拉框引用的 → 选项
  · 名字像"名单""字典""代码表""基础资料"的 → 参照表
  · 和别的 sheet 有同名编号列：这边每个编号一行、那边一个编号好几行 → 那边是明细表
  · 别的 sheet 里一个编号出现好几次、这边一个编号一行，而且表头像人或单位 → 对象表
  · 其余有数据的 → 单据表
- 表头完全一样的几张 sheet（按月分的） → 后面的记成 same_as 第一张。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet

from jacara.core.textvalue import parse_date
from jacara.templates import STARTERS, TemplateField, load_starter
from jacara.templates.extras import normalize

MAX_ROWS = 2000
"""每张 sheet 最多读多少行来分析（参照表的数据另外全读）。"""
MAX_COLUMNS = 80

_ID_HEADER = re.compile(
    r"(编号|号码|单号|代码|编码|序列号|\bID\b|\bNo\.?\b|\bcode\b)", re.IGNORECASE
)
_ENTITY_HEADER = re.compile(
    r"(病人|患者|受试者|员工|职工|客户|供应商|商户|会员|学生|人员|姓名|单位|公司|机构|中心|医院|实验室|站点)"
)
_REFERENCE_NAME = re.compile(
    r"(名单|字典|代码表|码表|基础|参照|对照|主数据|目录|lookup|master|code)", re.I
)
_OPTIONS_NAME = re.compile(r"(说明|填表|须知|选项|下拉|readme|instruction|option|list)", re.I)
_TOTAL_ROW = re.compile(r"^\s*(合计|总计|小计|共计|total|subtotal)", re.I)
_AUTO_HEADERS: dict[str, str] = {
    "序号": "seq",
    "行号": "seq",
    "no": "seq",
    "no.": "seq",
    "#": "seq",
    "文件名": "filename",
    "附件": "filename",
    "上传时间": "uploaded_at",
    "处理时间": "processed_at",
    "审核时间": "reviewed_at",
}
_MANUAL_HEADER = re.compile(
    r"(报销人|申请人|经办人|填报人|制表人|部门|审批|审核人|签字|签名|用途|事由|项目编号|成本中心|备注说明|核对人)"
)


@dataclass
class ColumnGuess:
    index: int
    header: str
    samples: list[str] = dc_field(default_factory=list)
    type: str = "string"
    source: str = "extract"
    """extract 从单据抽 / manual 人工填 / doc 单据上的字段（明细表里每行重复）/ auto 系统自动 /
    lookup 从参照表对照 / none 不填。"""
    field: str = ""
    label: str = ""
    auto: str = ""
    lookup: str = ""
    options: list[str] = dc_field(default_factory=list)
    description: str = ""
    aliases: list[str] = dc_field(default_factory=list)
    required: bool = False
    sensitive: bool = False
    unique: bool = False
    filled: int = 0


@dataclass
class SheetGuess:
    name: str
    role: str
    header_row: int
    rows: int
    hidden: bool = False
    same_as: str = ""
    key_header: str = ""
    """对象表、参照表：哪一列是编号；明细表：哪一列连到单据。"""
    match_field: str = ""
    """参照表：拿模板里哪个字段来对照。"""
    reason: str = ""
    columns: list[ColumnGuess] = dc_field(default_factory=list)


@dataclass
class Analysis:
    filename: str
    sheets: list[SheetGuess]
    starter: str = ""
    starter_title: str = ""


class WorkbookError(ValueError):
    pass


def open_workbook(data: bytes) -> Any:
    try:
        return load_workbook(io.BytesIO(data), data_only=True)
    except Exception as err:
        raise WorkbookError(
            "打不开这个 Excel。只支持 .xlsx；老的 .xls 请先在 Excel 里另存为 .xlsx"
        ) from err


def analyze(data: bytes, filename: str) -> Analysis:
    book = open_workbook(data)
    option_lists = _validation_options(book)
    sheets = [_sheet(ws, option_lists.get(ws.title, {})) for ws in book.worksheets]
    _guess_roles(sheets, _option_sources(book))
    starter = _match_starter(sheets)
    _assign_ids(sheets)
    return Analysis(
        filename=filename,
        sheets=sheets,
        starter=starter or "",
        starter_title=load_starter(starter).title if starter else "",
    )


# ---------- 读一张 sheet ----------


def _cells(ws: Worksheet) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row in ws.iter_rows(
        min_row=1, max_row=min(ws.max_row, MAX_ROWS), max_col=min(ws.max_column, MAX_COLUMNS)
    ):
        rows.append([c.value for c in row])
    return rows


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


_NUMBER = re.compile(r"^[-+(]?[¥$€£]?\s*[-+]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?%?\)?$")


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    text = _text(value)
    return bool(text) and any(ch.isdigit() for ch in text) and bool(_NUMBER.match(text))


def _is_date(value: Any) -> bool:
    if isinstance(value, date | datetime):
        return True
    text = _text(value)
    return bool(re.search(r"\d", text)) and len(text) <= 20 and parse_date(text) is not None


def _is_label(value: Any) -> bool:
    """像表头的格子：文字、不太长、不是数字或日期。"""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > 40:
        return False
    return not _is_number(text) and not _is_date(text)


def find_header_row(rows: list[list[Any]]) -> int:
    """表头在第几行（从 1 开始）。找不到返回 0。"""
    widest = max((sum(v is not None and _text(v) != "" for v in r) for r in rows[:30]), default=0)
    for i, row in enumerate(rows[:20]):
        labels = sum(_is_label(v) for v in row)
        filled = sum(_text(v) != "" for v in row)
        if labels >= 2 and labels == filled and labels >= max(2, int(widest * 0.6)):
            return i + 1
    return 0


def _headers(ws: Worksheet, rows: list[list[Any]], header_row: int) -> list[tuple[int, str]]:
    """（第几列，表头文字）。两层表头合成"上-下"。"""
    parents: dict[int, str] = {}
    if header_row > 1:
        for merged in ws.merged_cells.ranges:
            min_col, min_row, max_col, max_row = merged.bounds
            if min_row <= header_row - 1 <= max_row and max_col > min_col and max_row < header_row:
                label = _text(ws.cell(row=min_row, column=min_col).value)
                for col in range(min_col, max_col + 1):
                    parents[col] = label
    result: list[tuple[int, str]] = []
    for col, value in enumerate(rows[header_row - 1], start=1):
        text = _text(value)
        if not text and col in parents:
            text = parents[col]
        elif text and parents.get(col) and parents[col] != text:
            text = f"{parents[col]}-{text}"
        if text:
            result.append((col, text))
    return result


def data_rows(rows: list[list[Any]], header_row: int) -> list[list[Any]]:
    """表头下面的数据行：连着三行空行就算结束，合计行不算。"""
    result: list[list[Any]] = []
    blank = 0
    for row in rows[header_row:]:
        if all(_text(v) == "" for v in row):
            blank += 1
            if blank >= 3:
                break
            continue
        blank = 0
        first = next((_text(v) for v in row if _text(v)), "")
        if _TOTAL_ROW.match(first):
            continue
        result.append(row)
    return result


def _guess_type(values: list[Any]) -> str:
    present = [v for v in values if _text(v)]
    if not present:
        return "string"
    if all(_is_date(v) for v in present):
        return "date"
    if all(_is_number(v) for v in present):
        # 编号（发票号码）也是纯数字：表头像编号就当文字
        return "number"
    return "string"


def _sheet(ws: Worksheet, column_options: dict[int, list[str]]) -> SheetGuess:
    rows = _cells(ws)
    header_row = find_header_row(rows)
    hidden = ws.sheet_state != "visible"
    if header_row == 0:
        return SheetGuess(ws.title, "ignore", 0, 0, hidden, reason="没找到表头（说明、备注之类）")
    body = data_rows(rows, header_row)
    columns: list[ColumnGuess] = []
    for col, header in _headers(ws, rows, header_row):
        values = [r[col - 1] if col - 1 < len(r) else None for r in body]
        present = [_text(v) for v in values if _text(v)]
        kind = _guess_type(values)
        if kind == "number" and _ID_HEADER.search(header):
            kind = "string"
        columns.append(
            ColumnGuess(
                index=col,
                header=header,
                samples=present[:5],
                type=kind,
                label=header,
                options=column_options.get(col, []),
                unique=len(present) >= 1 and len(set(present)) == len(present),
                filled=len(present),
            )
        )
    return SheetGuess(ws.title, "document", header_row, len(body), hidden, columns=columns)


# ---------- 下拉选项（数据验证） ----------


def _validation_options(book: Any) -> dict[str, dict[int, list[str]]]:
    """每张 sheet 每一列的下拉选项：{sheet 名: {第几列: [选项]}}。"""
    result: dict[str, dict[int, list[str]]] = {}
    for ws in book.worksheets:
        for dv in ws.data_validations.dataValidation:
            if dv.type != "list" or not dv.formula1:
                continue
            options = _resolve_list(book, ws, dv.formula1)
            if not options:
                continue
            for cell_range in str(dv.sqref).split():
                min_col, _, max_col, _ = range_boundaries(cell_range)
                for col in range(min_col or 1, (max_col or min_col or 1) + 1):
                    result.setdefault(ws.title, {})[col] = options
    return result


def _option_sources(book: Any) -> set[str]:
    """被下拉框引用的 sheet（它们是选项表）。"""
    names: set[str] = set()
    for ws in book.worksheets:
        for dv in ws.data_validations.dataValidation:
            formula = str(dv.formula1 or "")
            if "!" in formula:
                names.add(formula.split("!")[0].lstrip("=").strip("'"))
    return names


def _resolve_list(book: Any, ws: Worksheet, formula: str) -> list[str]:
    formula = formula.strip()
    if formula.startswith('"') and formula.endswith('"'):
        return [o.strip() for o in formula.strip('"').split(",") if o.strip()]
    ref = formula.lstrip("=")
    sheet_name, _, cells = ref.rpartition("!")
    target = book[sheet_name.strip("'")] if sheet_name else ws
    try:
        min_col, min_row, max_col, max_row = range_boundaries(cells.replace("$", ""))
    except ValueError:
        return []
    values: list[str] = []
    for row in target.iter_rows(
        min_row=min_row,
        max_row=min(max_row or min_row or 1, (min_row or 1) + 500),
        min_col=min_col,
        max_col=max_col,
    ):
        values += [_text(c.value) for c in row if _text(c.value)]
    return list(dict.fromkeys(values))


# ---------- 角色 ----------


def _key(sheet: SheetGuess) -> ColumnGuess | None:
    """这张表的编号列：表头像编号的优先，其次值都不重复的。"""
    by_header = [c for c in sheet.columns if _ID_HEADER.search(c.header)]
    unique = (
        [c for c in by_header if c.unique] or by_header or [c for c in sheet.columns if c.unique]
    )
    return unique[0] if unique else None


def _guess_roles(sheets: list[SheetGuess], option_sheets: set[str]) -> None:
    for s in sheets:
        if s.header_row == 0 and s.name in option_sheets:
            s.role, s.reason = "options", "被下拉框引用（下拉选项）"
    data = [s for s in sheets if s.header_row > 0]
    for s in data:
        if s.name in option_sheets or (len(s.columns) <= 2 and _OPTIONS_NAME.search(s.name)):
            s.role, s.reason = "options", "被下拉框引用，或者像选项、说明"
        elif _OPTIONS_NAME.search(s.name):
            s.role, s.reason = "ignore", "名字像填表说明"
        elif _REFERENCE_NAME.search(s.name):
            s.role, s.reason = "reference", "名字像名单、字典、代码表"
            key = _key(s)
            s.key_header = key.header if key else (s.columns[0].header if s.columns else "")
        elif s.hidden:
            s.role, s.reason = "ignore", "隐藏的 sheet"

    # 表头一样的几张（按月分）
    seen: dict[tuple[str, ...], SheetGuess] = {}
    for s in data:
        if s.role not in ("document",):
            continue
        signature = tuple(normalize(c.header) for c in s.columns)
        if len(signature) >= 2 and signature in seen:
            s.same_as = seen[signature].name
            s.reason = f"表头和「{seen[signature].name}」一样"
        else:
            seen.setdefault(signature, s)

    candidates = [s for s in data if s.role == "document" and not s.same_as]
    # 同名的编号列：一边一个编号一行，一边一个编号好几行
    for a in candidates:
        for b in candidates:
            if a is b:
                continue
            for ca in a.columns:
                if not _ID_HEADER.search(ca.header) and not ca.unique:
                    continue
                cb = next(
                    (c for c in b.columns if normalize(c.header) == normalize(ca.header)), None
                )
                if cb is None or not ca.unique or cb.unique or cb.filled < 2:
                    continue
                # a 里一个编号一行，b 里同一个编号好几行
                entity_like = _ENTITY_HEADER.search(ca.header) or _ENTITY_HEADER.search(a.name)
                if entity_like and a.role == "document":
                    a.role, a.key_header = "entity", ca.header
                    a.reason = f"「{b.name}」里同一个「{ca.header}」出现好几次，这里一个一行"
                if b.role == "document":
                    b.role, b.key_header = "items", cb.header
                    b.reason = f"同一个「{cb.header}」有好几行，连到「{a.name}」"
    # 只有明细、没有单据表：明细里连接的那一列就是单据上的字段
    for s in data:
        if s.role == "items":
            for c in s.columns:
                if normalize(c.header) == normalize(s.key_header):
                    c.source = "doc"
        if s.role == "document" and not s.reason:
            s.reason = "一行对应一份单据（默认）"
    _guess_lookups(data)
    for s in data:
        for c in s.columns:
            auto = _AUTO_HEADERS.get(normalize(c.header))
            if auto:
                c.source, c.auto = "auto", auto
            elif _MANUAL_HEADER.search(c.header) and c.source == "extract":
                c.source = "manual"
        if s.role == "items" and any(o.role == "document" for o in data):
            # 明细表里和单据表同名的列：是单据上的字段，每行重复
            doc_headers = {
                normalize(c.header) for o in data if o.role == "document" for c in o.columns
            }
            for c in s.columns:
                if normalize(c.header) in doc_headers and c.source == "extract":
                    c.source = "doc"


def _guess_lookups(data: list[SheetGuess]) -> None:
    """参照表：别的 sheet 里和它同名的列。第一列拿来对照（match），其余的从参照表填（lookup）。"""
    for ref in (s for s in data if s.role == "reference"):
        columns = {normalize(c.header): c.header for c in ref.columns}
        for s in data:
            if s.role not in ("document", "items", "entity") or s.same_as:
                continue
            shared = [c for c in s.columns if normalize(c.header) in columns]
            if not shared:
                continue
            # 优先用名称这类单据上看得到的去对照，编号往往要靠对照才知道
            shared.sort(key=lambda c: bool(_ID_HEADER.search(c.header)))
            if not ref.match_field:
                ref.match_field = f"{s.name}::{shared[0].header}"
                ref.key_header = columns[normalize(shared[0].header)]
            for c in shared[1:]:
                c.source = "lookup"
                c.lookup = f"{ref.name}::{columns[normalize(c.header)]}"


# ---------- 对照起步模板 ----------


def _starter_fields(name: str) -> list[TemplateField]:
    t = load_starter(name)
    return list(t.fields) + [c for table in t.tables for c in table.columns]


def _match(field_: TemplateField, header: str) -> bool:
    wanted = normalize(header)
    return wanted in {normalize(field_.label), *(normalize(a) for a in field_.aliases)}


def _match_starter(sheets: list[SheetGuess]) -> str | None:
    """表头和哪个起步模板对上的最多（至少 2 列）。对上的列带上它的字段 ID、类型和说明。"""
    headers = [c for s in sheets if s.role in ("document", "items", "entity") for c in s.columns]
    best, best_score = None, 1
    for name in STARTERS:
        fields = _starter_fields(name)
        score = sum(any(_match(f, c.header) for f in fields) for c in headers)
        if score > best_score:
            best, best_score = name, score
    if best is None:
        return None
    fields = _starter_fields(best)
    for c in headers:
        found = next((f for f in fields if _match(f, c.header)), None)
        if found is not None:
            c.field = found.name
            c.type = found.type
            c.description = found.description
            c.aliases = [a for a in found.aliases if normalize(a) != normalize(c.header)]
            c.sensitive = found.sensitive
    return best


# ---------- 字段 ID ----------


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]
    return value if value and value[0].isalpha() else ""


def _assign_ids(sheets: list[SheetGuess]) -> None:
    """每一列一个字段 ID：起步模板对上的用它的，英文表头转成小写下划线，中文的编成 field_1……

    单据表、对象表、明细表里"单据上的字段"共用一套 ID（同一个表头就是同一个字段）；
    明细表自己的列在表里不重复就行。
    """
    used: dict[str, str] = {}
    counter = 0

    def doc_id(c: ColumnGuess) -> str:
        nonlocal counter
        key = normalize(c.header)
        if key in used:
            return used[key]
        candidate = c.field or slug(c.header)
        if not candidate:
            counter += 1
            candidate = f"field_{counter}"
        base, n = candidate, 2
        while candidate in used.values():
            candidate = f"{base}_{n}"
            n += 1
        used[key] = candidate
        return candidate

    for s in sheets:
        if s.role in ("document", "entity") and not s.same_as:
            for c in s.columns:
                if c.source not in ("auto", "none", "lookup"):
                    c.field = doc_id(c)
    for s in sheets:
        if s.role == "items":
            local: set[str] = set()
            for i, c in enumerate(s.columns, start=1):
                if c.source == "doc":
                    c.field = doc_id(c)
                elif c.source not in ("auto", "none", "lookup"):
                    candidate = c.field or slug(c.header) or f"col_{i}"
                    while candidate in local or candidate == "row":
                        candidate = f"{candidate}_{i}"
                    local.add(candidate)
                    c.field = candidate


def column_letter_index(letter: str) -> int:
    return column_index_from_string(letter)
