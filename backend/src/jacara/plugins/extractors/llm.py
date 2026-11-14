"""LLM extractor: sends the page structure and field list to the local LLM and receives field values
according to a JSON Schema.

The model sees the page structure: every text block with an index ⟨n⟩, laid out by its position
on the page (LAPDoc), followed by layout regions, label → value pairs and tables. For each value
the model names the blocks it used (LMDX), and evidence alignment verifies those blocks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any, cast

from jacara.core.i18n import stored
from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    FieldSpec,
    FieldType,
    TableSpec,
    cell_name,
    kv_name,
)
from jacara.plugins.model_backends import ChatMessage, ChatResult, ModelBackend
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import continuation_rows, group_rows
from jacara.structure.analyze import analyze_page
from jacara.structure.graph import PageStructure
from jacara.structure.serialize import spatial_text, structure_text

PROMPT_VERSION = "extract-v8"
"""Prompt version. Must change whenever the prompt below changes; recorded in results.

v1: OCR text blocks concatenated.
v2: merged into visual lines with line numbers; the model gives the line number of each value.
v3: table extraction (line items), one object per row, each cell with a line number.
v4: one line number per table row, cells as plain values (answers less than half as long). v3
answers on receipts with many items exceeded 4096 tokens and were truncated. Row count is
unbounded: tables must be complete.
v5: wrapped values: values spanning lines are joined, with row and row_end.
v6: text blocks: each block indexed and laid out as on the page, with layout regions, label →
value pairs and tables; each value names the blocks it used (blocks) instead of line numbers.
v7: multi-page documents: pages are concatenated with "page n" separators, with instructions to
continue tables across pages and ignore repeated headers, page headers and footers. Single-page
prompts are identical to v6.
v8: numbers, dates and date ranges carry text (the exact source notation) besides value (the
normalized value). The system verifies text in the source, then checks value against text,
instead of generating notations of value to search for. Date range values are {start, end}."""

SYSTEM_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的文字和要抽取的字段。
文字是按单据上的位置排开的：同一行的几块左右排开，上下对齐的就是一列，
每块文字前面的 ⟨数字⟩ 是这块的编号。
文字后面可能还有按位置找出来的"版面区域""标题 → 值""表格"，帮你看清结构，但不一定全对，以原文为准。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. 每个字段回答 value 和 blocks：blocks 是这个值用到的文字块编号（⟨数字⟩ 里的数字），按原文顺序。
   找不到的字段，value 填 null，blocks 填 []。
3. 文字类字段照抄原文，包括看起来像 OCR 错字的地方，不要改写或纠正。
   值跨好几块（地址、长的商品名折成两行）时，把这几块里属于它的文字按顺序连起来（中间加一个空格），
   blocks 写这几块。一块里只有一部分是值（"Date: 31/12/2017" 里的日期），
   blocks 写这一块，value 只写值。
4. 数字、日期、日期范围字段多回答一个 text：原文里这个值一字不差的写法
   （包括货币符号、空格、标点、月份英文，
   例如 "$4.11"、"August 3 , 2014"、"July 1 - July 31 , 2014"）；
   value 写你理解后的标准值：数字只填数字，不带货币符号和千分位逗号；日期写成 YYYY-MM-DD；
   日期范围的 value 写 start 和 end（都是 YYYY-MM-DD），原文只写了一次年份的，两边都用这个年份。
   原文看不出年份或者日月顺序的，不要猜：value 填 null，text 照抄。
5. 找不到的字段 value、text 都填 null。
6. 看清值在哪个标题下面、属于哪一栏：例如 Rooms 下面的数字是房间数，不是晚数。
7. 表格按原文顺序，一行一个对象，每一格回答 value 和 blocks；没有表格就给空列表。
   不要把小计、税、总额、找零当成表格的行，同一行商品不要重复写。商品名折成两行仍是同一行商品。"""

VISION_PROMPT_VERSION = PROMPT_VERSION + "+vision"
"""Version recorded when the model was shown the page image: the prompt adds VISION_NOTE below;
otherwise identical to extract-v7."""

VISION_NOTE = """另外附上了这张单据的原图（{n} 张，按页的顺序）。文字块是 OCR 从图上认出来的，
可能认错或漏掉（手写、印章、模糊、反光、褶皱的地方）。
- 原图和文字块一致：照常回答 value 和 blocks。
- OCR 认错了：value 写你在原图上看到的，blocks 写认错的那几块。
- OCR 漏掉了（文字块里没有）：value 写你在原图上看到的，blocks 写 []。
- 原图上也看不清的，value 填 null，不要猜。"""

CLASSIFY_PROMPT_VERSION = "classify-v1"
"""Prompt version for automatic template selection."""

CLASSIFY_PROMPT = """你在给单据分类。用户给你一张单据开头的文字，和几个编了号的模板
（每个模板有名字、说明、功能领域、国家和地区、要抽的字段）。
选一个最适合这张单据的模板：单据的种类要对上（发票、小票、账单、行程单、报告……），
国家和地区也要对上（写着"通用"的模板不分国家）。
没有一个合适就选 0，不要勉强。reason 用一句中文说明为什么。只根据给出的文字判断。"""

CLASSIFY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"choice": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["choice", "reason"],
}

_TYPE_NAMES = {"string": "文字", "number": "数字", "date": "日期", "date_range": "日期范围"}

_BLOCKS: dict[str, object] = {"type": "array", "items": {"type": "integer"}}
"""Indices of the text blocks used."""
_DATE: dict[str, object] = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
_TEXT: dict[str, object] = {"anyOf": [{"type": "string"}, {"type": "null"}]}
"""Source notation: verbatim, or null."""

DISCOVER_PROMPT_VERSION = "discover-v5"
"""Prompt version for key-value discovery. v2: values spanning lines (row_end). v3: text blocks
(blocks), as in extract-v6. v4: date ranges and dates within sentences are copied verbatim
instead of rewritten as YYYY-MM-DD; blocks lists only blocks used by the value, not the label
block. Date ranges have type date_range. v5: each item adds text (the exact source notation);
value holds the normalized value."""

DISCOVER_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的文字。
文字是按单据上的位置排开的，每块文字前面的 ⟨数字⟩ 是这块的编号；后面可能还有按位置找出来的
"标题 → 值"和表格，帮你看清结构，以原文为准。
任务：找出单据上所有"名称：值"形式的信息（例如 发票号码、日期、合计、地址、电话），
每一项给出 key（单据上印的名称，照抄原文）、value（值）、text（值在原文里一字不差的写法）、type、
blocks（值用到的文字块编号）。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. key 用单据上的原话；单据上没有印名称、但意思明确的值（例如最上面的商家名），
   key 用简短的中文说明，例如"商家名称"。值写在标题下面的，key 用那个标题。
3. text 永远照抄原文（包括货币符号、空格、标点、月份英文）。value 和 type：
   金额、数量这类纯数字写 number，value 只填数字，不带货币符号和千分位逗号；
   单独一个日期写 date，value 写成 YYYY-MM-DD；
   日期范围（July 1 - July 31, 2014、2014年7月1日至31日）写 date_range，
   value 写成 "开始 ~ 结束"（都是 YYYY-MM-DD，原文只写了一次年份的两边都用这个年份）；
   其他写 string，value 和 text 一样照抄原文。看不出年份或日月顺序的，不要猜，type 写 string。
4. 值跨几块（例如地址折成两行）时连起来写（中间加一个空格），blocks 写这几块；
   只写值用到的块，不写印着名称的那块，也不写旁边别的栏的块。
5. 商品明细这种表格不要逐格列出，每一行商品也不要当成键值对；同一项不要重复写。"""

DISCOVER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]},
                    "text": _TEXT,
                    "type": {"enum": ["string", "number", "date", "date_range"]},
                    "blocks": _BLOCKS,
                },
                "required": ["key", "value", "text", "type", "blocks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pairs"],
    "additionalProperties": False,
}


def _plain_schema(f: FieldSpec) -> dict[str, object]:
    """Value only: number, date, text, or null."""
    if f.type == "number":
        value: dict[str, object] = {"type": "number"}
    elif f.type == "date":
        value = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    else:
        value = {"type": "string"}
    return {"anyOf": [value, {"type": "null"}]}


def _value_schema(f: FieldSpec) -> dict[str, object]:
    """One value: {"value", "blocks"}; numbers, dates and date ranges also have text (the source
    notation)."""
    if f.type == "string":
        return {
            "type": "object",
            "properties": {"value": _plain_schema(f), "blocks": _BLOCKS},
            "required": ["value", "blocks"],
            "additionalProperties": False,
        }
    if f.type == "date_range":
        either: dict[str, object] = {"anyOf": [_DATE, {"type": "null"}]}
        value: dict[str, object] = {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {"start": either, "end": either},
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        }
    else:
        value = _plain_schema(f)
    return {
        "type": "object",
        "properties": {"value": value, "text": _TEXT, "blocks": _BLOCKS},
        "required": ["value", "text", "blocks"],
        "additionalProperties": False,
    }


def _row_schema(columns: Sequence[FieldSpec]) -> dict[str, object]:
    """One table row: {column name: {"value", "blocks"}, ...}."""
    return {
        "type": "object",
        "properties": {c.name: _value_schema(c) for c in columns},
        "required": [c.name for c in columns],
        "additionalProperties": False,
    }


def build_schema(
    fields: Sequence[FieldSpec], tables: Sequence[TableSpec] = ()
) -> dict[str, object]:
    """Field list → JSON Schema: {"value", "blocks"} per field and one list per table. All are
    required."""
    properties: dict[str, object] = {f.name: _value_schema(f) for f in fields}
    for t in tables:
        properties[t.name] = {"type": "array", "items": _row_schema(t.columns)}
    return {
        "type": "object",
        "properties": properties,
        "required": [*(f.name for f in fields), *(t.name for t in tables)],
        "additionalProperties": False,
    }


def _field_line(f: FieldSpec, indent: str = "") -> str:
    return f"{indent}- {f.name}（{_TYPE_NAMES[f.type]}）：{f.description}"


MULTI_PAGE_NOTE = (
    "这张单据有 {n} 页，下面按顺序接在一起，页与页之间有“第 n 页”分隔。"
    "字段在哪一页都可以；表格跨页时接着写，每页重复的表头、页眉页脚、“承上页”“续下页”这类行不算表格的行。"
)


def page_prompt(structure: PageStructure) -> str:
    """Document shown to the model: layout-preserving text plus structure. Stacked documents first
    state the number of pages."""
    text = f"单据文字（按位置排开，⟨数字⟩ 是文字块编号）：\n{spatial_text(structure)}"
    if len(structure.page_starts) > 1:
        text = MULTI_PAGE_NOTE.format(n=len(structure.page_starts)) + "\n\n" + text
    extra = structure_text(structure)
    return f"{text}\n\n{extra}" if extra else text


def sent_record(
    structure: PageStructure, document_text: str, images: int = 0, lessons: int = 0
) -> dict[str, Any]:
    """Model input: text block indices included in the layout text, the document text and its
    sha256, number of images and examples. Stored on the extraction to check whether a value was
    in the text sent."""
    numbers = sorted(
        structure.number_of(c) for line in structure.lines for c in structure.blocks[line].children
    )
    return {
        "version": 1,
        "blocks": numbers,
        "total_blocks": len(structure.words),
        "chars": len(document_text),
        "sha256": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
        "text": document_text,
        "images": images,
        "lessons": lessons,
    }


def build_messages(
    page: OcrPage,
    fields: Sequence[FieldSpec],
    tables: Sequence[TableSpec] = (),
    structure: PageStructure | None = None,
    images: Sequence[bytes] = (),
    lessons: str = "",
) -> list[ChatMessage]:
    """Build the messages sent to the model: system rules + field and table descriptions + document
    (layout text + structure).

        lessons are past manual corrections, placed before the document.

        images are page images, appended with a note that the image prevails where OCR may have
    misread."""
    structure = structure or analyze_page(page)
    field_lines = "\n".join(_field_line(f) for f in fields)
    for t in tables:
        columns = "\n".join(_field_line(c, "  ") for c in t.columns)
        field_lines += f"\n- {t.name}（表格，每行一个对象）：{t.description}\n{columns}"
    learned = f"{lessons}\n\n" if lessons else ""
    user = f"要抽取的字段：\n{field_lines}\n\n{learned}{page_prompt(structure)}"
    if images:
        user += "\n\n" + VISION_NOTE.format(n=len(images))
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user, tuple(images))]


SEGMENT_PROMPT_VERSION = "segment-v1"
"""Prompt version for deciding whether a page continues the previous page."""

SEGMENT_PROMPT = """你在整理扫描或导出的单据。一个文件里可能有好几张单据，一张单据也可能有好几页。
用户给你上一页的最后几行和这一页的最前几行。判断这一页是上一页那张单据的续页（同一张单据的下一页），
还是一张新的单据。
- 续页：接着上一页的明细表往下写、没有新的单据抬头、写着“续”“承上页”、页码是第 2 页及以后。
- 新单据：有自己的抬头（商家、发票号码、单据标题）、单号或日期和上一页不同、
  上一页已经有合计或签名结尾。
只根据给出的文字判断。"""

SEGMENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"continues": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["continues", "reason"],
}

SEGMENT_LINES = 25
"""Lines shown to the model for continuation detection: the last lines of the previous page and the
first lines of this page."""


LOOK_PROMPT_VERSION = "look-v1"
"""Prompt version for "ask the model about this region" during review."""

LOOK_PROMPT = """你在帮人核对单据。用户给你单据上框出来的一小块图，
可能有手写、印章、模糊、被笔迹盖住的字。
1. text：照原样写出图里的文字（多行用空格连起来），看不清的字不要猜，写 ?。
2. value：如果用户说了要填哪个字段，写这个字段的值：数字只写数字（不带货币符号和千分位逗号），
   日期写成 YYYY-MM-DD，文字照抄。图里没有这个字段的值就写 null。"""

LOOK_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]},
    },
    "required": ["text", "value"],
}


def look_at(
    backend: ModelBackend, image: bytes, label: str | None = None, kind: str | None = None
) -> tuple[str, str | None, str]:
    """Show the model a small image region: returns (text in the image, value of the target field,
    model name)."""
    ask = "读出图里的文字。"
    if label:
        ask += f"要填的字段：{label}（{_TYPE_NAMES.get(kind or 'string', '文字')}）。"
    messages = [ChatMessage("system", LOOK_PROMPT), ChatMessage("user", ask, (image,))]
    result = backend.chat(messages, json_schema=LOOK_SCHEMA)
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError as err:
        raise ExtractionError(
            stored("模型输出不是有效的 JSON：{answer}", answer=result.text[:200])
        ) from err
    if not isinstance(data, dict):
        raise ExtractionError(stored("模型输出不是 JSON 对象：{answer}", answer=result.text[:200]))
    text = " ".join(str(data.get("text") or "").split())
    value = data.get("value")
    if isinstance(value, float) and value == int(value) and kind != "number":
        value = int(value)
    shown = None if value is None or (isinstance(value, str) and not value.strip()) else str(value)
    return text, shown, result.model


class LlmExtractor:
    name = "llm"

    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend
        self.on_text: Callable[[int], None] | None = None
        """Callback receiving the total characters written whenever the model writes a chunk (for
        progress). Not streamed when unset."""
        self.lessons: tuple[str, int] = ("", 0)
        """Past manual corrections: (prompt section, count). Set by the pipeline before extraction
        and cleared afterwards."""
        self.lessons_mode = "recent"
        """How examples were selected: with curated, the prompt version records +curatedN."""

    def _ask(self, messages: list[ChatMessage], schema: dict[str, object]) -> ChatResult:
        """Query the model. Streams when progress is requested and the model server supports
        streaming; otherwise fetches the whole answer."""
        stream = getattr(self.backend, "chat_stream", None)
        if self.on_text is not None and callable(stream):
            return cast(ChatResult, stream(messages, json_schema=schema, on_text=self.on_text))
        return self.backend.chat(messages, json_schema=schema)

    def continues(self, previous: OcrPage, current: OcrPage) -> bool | None:
        """Whether this page continues the previous page's document. None when the model cannot
        answer (treated as a new document)."""
        before = "\n".join(r.text for r in group_rows(previous)[-SEGMENT_LINES:])
        after = "\n".join(r.text for r in group_rows(current)[:SEGMENT_LINES])
        messages = [
            ChatMessage("system", SEGMENT_PROMPT),
            ChatMessage("user", f"上一页的最后几行：\n{before}\n\n这一页的最前几行：\n{after}"),
        ]
        result = self.backend.chat(messages, json_schema=SEGMENT_SCHEMA)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return None
        answer = data.get("continues") if isinstance(data, dict) else None
        return answer if isinstance(answer, bool) else None

    def sees_images(self) -> bool:
        """Whether the model can process images (the model server has a vision projector loaded)."""
        check = getattr(self.backend, "sees_images", None)
        return bool(check()) if callable(check) else False

    def choose(self, text: str, options: Sequence[str]) -> tuple[int, str] | None:
        """Automatic template selection: returns (index, 1-based, 0 if none fits; reason). None if
        the model answer is invalid."""
        listed = "\n".join(f"{n}. {o}" for n, o in enumerate(options, start=1))
        messages = [
            ChatMessage("system", CLASSIFY_PROMPT),
            ChatMessage("user", f"模板：\n{listed}\n\n单据开头的文字：\n{text}"),
        ]
        result = self.backend.chat(messages, json_schema=CLASSIFY_SCHEMA)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        choice, reason = data.get("choice"), data.get("reason")
        if not isinstance(choice, int) or isinstance(choice, bool):
            return None
        if not 0 <= choice <= len(options):
            return None
        return choice, reason if isinstance(reason, str) else ""

    def extract(
        self,
        page: OcrPage,
        fields: Sequence[FieldSpec],
        tables: Sequence[TableSpec] = (),
        structure: PageStructure | None = None,
        images: Sequence[bytes] = (),
    ) -> ExtractionResult:
        structure = structure or analyze_page(page)
        schema = build_schema(fields, tables)
        lessons, count = self.lessons
        result = self._ask(build_messages(page, fields, tables, structure, images, lessons), schema)
        sent = sent_record(structure, page_prompt(structure), len(images), count)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as err:
            raise ExtractionError(
                stored("模型输出不是有效的 JSON：{answer}", answer=result.text[:200])
            ) from err
        if not isinstance(data, dict):
            raise ExtractionError(
                stored("模型输出不是 JSON 对象：{answer}", answer=result.text[:200])
            )
        data = cast(dict[str, Any], data)

        extracted = [_field(f, data.get(f.name), f.name, result.text, structure) for f in fields]
        marks = continuation_rows(group_rows(page))
        for t in tables:
            rows = data.get(t.name, [])
            if not isinstance(rows, list):
                raise ExtractionError(
                    stored(
                        "模型输出中的 {name} 不是列表：{answer}",
                        name=t.name,
                        answer=result.text[:200],
                    )
                )
            items: list[list[ExtractedField]] = []
            for row in cast(list[Any], rows):
                if not isinstance(row, dict):
                    raise ExtractionError(stored("模型输出中的 {name} 有一行不是对象", name=t.name))
                row = cast(dict[str, Any], row)
                if not all(c.name in row for c in t.columns):
                    raise ExtractionError(
                        stored("模型输出中的 {name} 有一行缺少列：{row}", name=t.name, row=row)
                    )
                cells = [
                    _field(c, _cell(row, c.name), c.name, result.text, structure) for c in t.columns
                ]
                if all(c.value is None for c in cells):
                    continue  # A completely empty row is not a row.
                if items and _continues_item(items[-1], cells, t, marks):
                    items[-1] = _join_item(items[-1], cells, structure)
                    continue
                items.append(cells)
            for index, cells in enumerate(items, start=1):
                extracted += [
                    _renamed(cell, cell_name(t.name, index, c.name))
                    for cell, c in zip(cells, t.columns, strict=True)
                ]

        return ExtractionResult(
            fields=tuple(extracted),
            raw_output=result.text,
            model=result.model,
            backend=result.backend,
            prompt_version=(VISION_PROMPT_VERSION if images else PROMPT_VERSION)
            + (
                f"+{'curated' if self.lessons_mode == 'curated' else 'lessons'}{count}"
                if count
                else ""
            ),
            duration_ms=result.duration_ms,
            sent=sent,
        )

    def discover(self, page: OcrPage, structure: PageStructure | None = None) -> ExtractionResult:
        """Key-value discovery (the first step of creating a template from a sample): find every
        "label: value" pair on the document."""
        return _discover(self, page, structure or analyze_page(page))


def _discover(self: LlmExtractor, page: OcrPage, structure: PageStructure) -> ExtractionResult:
    messages = [
        ChatMessage("system", DISCOVER_PROMPT),
        ChatMessage("user", page_prompt(structure)),
    ]
    result = self._ask(messages, DISCOVER_SCHEMA)
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError as err:
        raise ExtractionError(
            stored("模型输出不是有效的 JSON：{answer}", answer=result.text[:200])
        ) from err
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, list):
        raise ExtractionError(
            stored("模型输出中没有 pairs 列表：{answer}", answer=result.text[:200])
        )
    fields: list[ExtractedField] = []
    seen: set[tuple[str, str]] = set()
    for item in cast(list[Any], pairs):
        if not isinstance(item, dict):
            continue
        item = cast(dict[str, Any], item)
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        if not key or value is None or str(value).strip() == "":
            continue
        type_: FieldType = item["type"] if item.get("type") in _TYPE_NAMES else "string"
        if type_ == "number":
            value = _as_number(value)
            if value is None:
                type_, value = "string", str(item.get("value"))
        elif type_ == "date":
            # The model sometimes copies a single date verbatim (August 3 , 2014); convert to
            # YYYY-MM-DD, or keep as text if unparseable.
            value = str(value).strip()
            day = _as_date(value)
            if day is None:
                type_ = "string"
            else:
                value = day
        elif type_ == "date_range":
            ranged = _range_value(str(value))
            if ranged is None or "~" not in ranged:
                type_, value = "string", str(value).strip()
            else:
                value = ranged
        else:
            value = str(value).strip()
        text = item.get("text")
        text = " ".join(text.split()) if isinstance(text, str) and text.strip() else None
        original = str(item.get("value")).strip()
        if text is None and type_ != "string" and original != str(value):
            # The model put the source text into value: use it as the source text.
            text = " ".join(original.split())
        if (key, str(value)) in seen:
            continue  # The same item was written twice.
        seen.add((key, str(value)))
        blocks = _blocks(item, structure)
        row, row_end = _rows_of(blocks, structure) if blocks else _legacy_rows(item)
        fields.append(
            ExtractedField(
                name=kv_name(len(fields) + 1),
                type=type_,
                value=value,
                row=row,
                label=key[:200],
                row_end=row_end,
                blocks=blocks,
                text=text if type_ != "string" else None,
            )
        )
    return ExtractionResult(
        fields=tuple(fields),
        raw_output=result.text,
        model=result.model,
        backend=result.backend,
        prompt_version=DISCOVER_PROMPT_VERSION,
        duration_ms=result.duration_ms,
        sent=sent_record(structure, messages[1].content),
    )


def _as_date(value: str) -> str | None:
    """Format a date as YYYY-MM-DD; None if unparseable."""
    from datetime import date

    from jacara.core.textvalue import parse_date

    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        day = parse_date(value)
        return day.isoformat() if day else None


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    from jacara.core.textvalue import parse_number

    return parse_number(str(value))


def _blocks(item: dict[str, Any], structure: PageStructure) -> tuple[int, ...]:
    """Block indices given by the model: only existing ones, without duplicates, in source order
    (indices follow reading order)."""
    raw = item.get("blocks")
    if not isinstance(raw, list):
        return ()
    found = {
        n
        for n in cast(list[Any], raw)
        if isinstance(n, int) and not isinstance(n, bool) and structure.word(n) is not None
    }
    return tuple(sorted(found))


def _rows_of(blocks: tuple[int, ...], structure: PageStructure) -> tuple[int | None, int | None]:
    """Text blocks → (first line, last line). The last line is None for a single line."""
    rows = sorted(int(w.attrs["row"]) for n in blocks if (w := structure.word(n)) is not None)
    if not rows:
        return None, None
    return rows[0], (rows[-1] if rows[-1] != rows[0] else None)


def _field(
    spec: FieldSpec, item: object, name: str, raw: str, structure: PageStructure
) -> ExtractedField:
    """One {"value", "blocks"} item of the model answer → ExtractedField. name is the stored field
    name."""
    if not isinstance(item, dict) or "value" not in item:
        raise ExtractionError(
            stored("模型输出缺少字段 {name}：{answer}", name=name, answer=raw[:200])
        )
    item = cast(dict[str, Any], item)
    value = item["value"]
    # No text, and value is copied source text (legacy format, or instructions ignored): use it as
    # the source text.
    copied = value.strip() if isinstance(value, str) and spec.type != "string" else None
    if spec.type == "number" and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if spec.type == "date" and isinstance(value, str):
        # Dates are stored as YYYY-MM-DD, including verbatim copies (August 3 , 2014); unparseable
        # dates are kept as is and flagged during verification.
        value = _as_date(value.strip()) or value
    if spec.type == "date_range":
        value = _range_value(value)
    text = item.get("text")
    text = " ".join(text.split()) if isinstance(text, str) and text.strip() else None
    if text is None and copied and copied != value:
        text = " ".join(copied.split())
    if value is None and text is not None and spec.type != "string":
        # The model left it empty because it was unsure: parse the source text once; still empty if
        # unparseable.
        value = _from_text(spec.type, text)
    blocks = _blocks(item, structure) if value is not None else ()
    row, row_end = _rows_of(blocks, structure)
    if not blocks and value is not None:
        row, row_end = _legacy_rows(item)
    return ExtractedField(
        name=name,
        type=spec.type,
        value=value,
        row=row,
        row_end=row_end,
        blocks=blocks,
        text=text if value is not None else None,
    )


def _range_value(value: object) -> str | None:
    """Date range value: {"start", "end"} → "2014-07-01 ~ 2014-07-31" (legacy source-text strings
    are accepted too)."""
    from jacara.core.daterange import parse_date_range

    if isinstance(value, dict):
        start = _as_date(str(value.get("start") or "")) if value.get("start") else None
        end = _as_date(str(value.get("end") or "")) if value.get("end") else None
        if start is None and end is None:
            return None
        return f"{start or ''} ~ {end or ''}".strip()
    if isinstance(value, str) and value.strip():
        found = parse_date_range(value).range
        return found.text() if found and found.start and found.end else value.strip()
    return None


def _from_text(type_: str, text: str) -> str | float | None:
    """The model set value to null and gave only the source text: parse it once by rule (None if
    unparseable)."""
    from jacara.core.daterange import parse_date_range

    if type_ == "number":
        return _as_number(text)
    if type_ == "date":
        return _as_date(text)
    found = parse_date_range(text).range
    if found is None or found.start is None or found.end is None:
        return None
    return found.text()


def _cell(row: dict[str, Any], column: str) -> object:
    """One table cell. Occasional answers in the legacy format (plain values, line number on the
    row) are accepted too."""
    cell = row[column]
    if isinstance(cell, dict):
        return cell
    return {"value": cell, "row": row.get("row"), "row_end": row.get("row_end")}


def _legacy_rows(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """No blocks given but legacy line numbers (row, row_end of extract-v5): kept as hints for the
    text search."""

    def number(value: object) -> int | None:
        ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        return cast(int, value) if ok else None

    row, end = number(item.get("row")), number(item.get("row_end"))
    return row, (end if row is not None and end is not None and end > row else None)


def _renamed(cell: ExtractedField, name: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        type=cell.type,
        value=cell.value,
        row=cell.row,
        label=cell.label,
        row_end=cell.row_end,
        blocks=cell.blocks,
        text=cell.text,
    )


def _continues_item(
    previous: list[ExtractedField],
    row: list[ExtractedField],
    table: TableSpec,
    marks: frozenset[int],
) -> bool:
    """The model still split a wrapped item name into two items: this "row" has only text and no
    numbers, immediately follows the previous item, and by position continues it (line numbers in
    marks).

        For example, the previous row is "Room Charges Deluxe  1  1939  1939" and this row only
    "Double with Breakfast". Section headings ("Beverages") sit above items rather than
    continuing a line and are not merged."""
    lines = [c.row for c in row if c.row is not None]
    ends = [end for c in previous if (end := c.row_end or c.row) is not None]
    last = max(ends, default=None)
    if not lines or last is None or min(lines) != last + 1 or min(lines) not in marks:
        return False
    texts = [i for i, c in enumerate(table.columns) if c.type == "string"]
    others = [i for i, c in enumerate(table.columns) if c.type != "string"]
    return any(row[i].value is not None for i in texts) and all(
        row[i].value is None for i in others
    )


def _join_item(
    previous: list[ExtractedField], row: list[ExtractedField], structure: PageStructure
) -> list[ExtractedField]:
    """Merge the wrapped row into the previous item: text appended (separated by one space), blocks
    merged."""
    joined: list[ExtractedField] = []
    for before, extra in zip(previous, row, strict=True):
        if before.type == "string" and extra.value is not None:
            value = f"{before.value} {extra.value}" if before.value else extra.value
            blocks = tuple(sorted({*before.blocks, *extra.blocks}))
            if blocks:
                row_no, row_end = _rows_of(blocks, structure)
            # Legacy format (line numbers only): the first line is unchanged and the last line is
            # this one.
            else:
                row_no, row_end = before.row, extra.row_end or extra.row
            joined.append(
                ExtractedField(
                    name=before.name,
                    type=before.type,
                    value=value,
                    row=row_no,
                    row_end=row_end,
                    blocks=blocks,
                )
            )
        else:
            joined.append(before)
    return joined
