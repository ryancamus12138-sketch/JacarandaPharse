"""按模板字段逐一核对（ADR 0042）：PDF 文字层、表单字段、局部放大再识别，都只当作候选。

证据对齐（BlockAligner）已经在 OCR 结果里找到了出处。这里再做三件事：
1. 有出处的值：和 PDF 文字层同一位置的字比（按字段类型比，一位不同就算不一样）。
   一样 → 只记一笔"文字层也一致"；对不上（附近好几行字）→ 关键字段标黄；
   不一样 → 局部放大再识别一次，所有不同的读法都留作候选，关键字段一律标黄等人确认。
   文字层不会替换 OCR 的值：字体对照表错一位的 PDF，文字层写的 1234567898 和图上的
   1234567890 整行相似度有 0.94，按相似度采用文字层就会把对的改成错的。
2. 模型没给值的字段（按模板遍历，不只看模型的回答）：在原文里找这个字段的标签，
   标签旁边有像值的字（OCR、文字层、局部放大再识别）就作为候选、标黄；找不到标签、
   字段又是必填的，留空待审。整行漏掉的"合计"，只看模型的回答永远发现不了。
3. PDF 表单字段存的值：和字段对上名字的，值不一样或模型没给时作为候选；
   字段设了隐藏的标"页面不可见"，没隐藏但 OCR 没读到的标"可见性待确认"。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from itertools import pairwise

from jacara.core.cjk import plain_cjk
from jacara.core.daterange import parse_date_range
from jacara.core.dates import date_pattern
from jacara.core.numbers import number_pattern
from jacara.core.textvalue import parse_date, parse_number
from jacara.plugins.aligners.base import AlignedField, Candidate
from jacara.plugins.aligners.crosscheck import cross_check
from jacara.plugins.aligners.text_match import (
    _best_similarity,
    _squash,
    _tidy,
)
from jacara.plugins.extractors.base import ExtractedField
from jacara.plugins.ocr.base import FormValue, OcrLine, OcrPage
from jacara.plugins.validators.base import Validator
from jacara.templates import Template, TemplateField

Zoomer = Callable[[Sequence[float]], Candidate | None]
"""局部放大再识别：长页上的框 → 候选（没认出字是 None）。"""

KEY_TYPES = frozenset({"number", "date", "date_range"})
"""关键字段：金额、数量、检验结果这些数字，日期、日期范围；还有模板里标了"不能重复"的（单号）。
有冲突时第一版一律要人确认。"""

OVERLAP = 0.5
"""文字层的一段和出处框重叠的面积，占两者中小的那个的一半以上，才算同一个位置。"""

SIMILAR = 0.9
"""非关键的文字字段（名称、地址）：文字层和值相似到这个程度就算一样（标点、空格常有出入）。"""

NEAR = 12
"""找漏读的值时，从标签往右最多看几个字高的宽度。"""


def is_key(type_: str, spec: TemplateField | None) -> bool:
    return type_ in KEY_TYPES or bool(spec is not None and spec.unique)


def check_fields(
    aligned: Sequence[AlignedField],
    page: OcrPage,
    template: Template | None,
    zoom: Zoomer | None = None,
    day_first: bool | None = None,
) -> tuple[AlignedField, ...]:
    specs = {f.name: f for f in template.fields} if template is not None else {}
    labels = frozenset(
        _squash(n).rstrip(".:：#")
        for f in specs.values()
        for n in (f.label, *f.aliases)
        if n and len(n) >= 2
    )
    done: list[AlignedField] = []
    for a in aligned:
        spec = specs.get(a.field.name)
        if a.status == "computed":
            done.append(a)
            continue
        key = is_key(a.field.type, spec)
        if a.evidence is not None and a.field.value is not None:
            a = _check_value(a, page, zoom, key, day_first)
        elif spec is not None and a.field.value is None and spec.asks_model:
            a = _missing(a, spec, page, zoom, labels)
        if spec is not None and page.forms:
            a = _check_form(a, spec, page)
        done.append(a)
    return tuple(done)


# ---------- 1. 有出处的值：和文字层比 ----------


def _check_value(
    a: AlignedField, page: OcrPage, zoom: Zoomer | None, key: bool, day_first: bool | None
) -> AlignedField:
    if not page.text_layer or a.evidence is None:
        return replace(a, layer="none")
    box = a.evidence.bbox
    hits = sorted(
        (ln for ln in page.text_layer if overlap(ln.bbox, box) >= OVERLAP),
        key=lambda ln: (ln.bbox[1], ln.bbox[0]),
    )
    if not hits:
        return replace(a, layer="absent")
    layer_text = " ".join(ln.text for ln in hits)
    if supports(a.field.type, a.field.value, layer_text, key, day_first):
        return replace(a, layer="agree")
    ocr_text = " ".join(ln.text for ln in a.evidence.lines)
    if _rows(hits) > 1:
        note = f"PDF 文字层这里有好几行字（「{layer_text}」），对不上是哪一行，没法核对"
        return replace(
            a,
            layer="ambiguous",
            needs_review=a.needs_review or key,
            note=_join(a.note, note + ("，请核对" if key else "")),
        )
    layer_box = _union([ln.bbox for ln in hits])
    candidates = [
        Candidate("ocr", ocr_text, box, a.evidence.confidence, _value(a.field.type, ocr_text)),
        Candidate("text_layer", layer_text, layer_box, None, _value(a.field.type, layer_text)),
    ]
    zoomed = zoom(_union([box, layer_box])) if zoom is not None else None
    # 走到这里是"文字层里没有现在的值"，不一定是文字层和 OCR 不一样：
    # 两边一样、只是和值对不上时要说清楚
    same = _squash(plain_cjk(layer_text)) == _squash(plain_cjk(ocr_text))
    if same:
        note = f"PDF 文字层和 OCR 这里都是「{layer_text}」，和现在的值「{a.field.value}」对不上"
    else:
        note = f"PDF 文字层这里写的是「{layer_text}」，和 OCR 读的「{ocr_text}」不一样"
    zoom_agrees = False
    if zoomed is not None:
        zoomed = replace(zoomed, value=_value(a.field.type, zoomed.text))
        candidates.append(zoomed)
        zoom_agrees = supports(a.field.type, a.field.value, zoomed.text, key, day_first)
        layer_value = _value(a.field.type, layer_text)
        if zoom_agrees:
            verdict = "，和现在的值一致"
        elif supports(a.field.type, layer_value, zoomed.text, key, day_first):
            verdict = "，和文字层、OCR 一致" if same else "，和文字层一致"
        elif supports(a.field.type, _value(a.field.type, ocr_text), zoomed.text, key, day_first):
            verdict = "，和 OCR 一致"
        else:
            verdict = "，和前两处也不一样" if same else "，三处都不一样"
        note += f"；{zoomed.note}读成「{zoomed.text}」{verdict}"
    review = key or not zoom_agrees
    return replace(
        a,
        layer="conflict",
        candidates=distinct(candidates),
        needs_review=a.needs_review or review,
        note=_join(a.note, note + ("，请核对" if review else "")),
    )


def supports(
    type_: str, value: object, text: str, key: bool = True, day_first: bool | None = None
) -> bool:
    """text 里有没有 value 这个值（按字段类型比）。比之前两边都先把部首码位换成平常的字。"""
    if value is None or not text.strip():
        return False
    text = plain_cjk(text)
    if type_ == "number":
        number = value if isinstance(value, (int, float)) else parse_number(str(value))
        if number is None:
            return False
        return number_pattern(number, loose=True).search(_tidy(text)) is not None
    if type_ == "date":
        if date_pattern(value).search(_tidy(text)):
            return True
        return cross_check("date", value, text, day_first).verdict == "ok"
    if type_ == "date_range":
        return cross_check("date_range", value, text, day_first).verdict == "ok"
    wanted = _squash(plain_cjk(str(value)))
    have = _squash(text)
    if not wanted:
        return False
    if wanted in have:
        return True
    return not key and _best_similarity(wanted, have) >= SIMILAR


# ---------- 2. 模型没给值的字段：按标签找 ----------


def _missing(
    a: AlignedField,
    spec: TemplateField,
    page: OcrPage,
    zoom: Zoomer | None,
    labels: frozenset[str] = frozenset(),
) -> AlignedField:
    label = _find_label(spec, page.lines)
    if label is None:
        if spec.required:
            note = "模板要求填写，原文里没找到这个字段的标签，留空待审"
            return replace(a, needs_review=True, note=_join(a.note, note))
        return a
    line, rest = label
    rest = _cut_at_labels(rest, labels)
    height = max(1.0, line.bbox[3] - line.bbox[1])
    x0, y0, x1, y1 = line.bbox
    # 同一行往右一直到页边（两栏排版时值常常靠右对齐）；右边没有，再看标签正下方
    right = (x1, y0 - 0.3 * height, float(page.width), y1 + 0.3 * height)
    below = (x0 - height, y1, x0 + NEAR * height, y1 + 1.8 * height)

    def first_value(lines: Sequence[OcrLine]) -> OcrLine | None:
        for region in (right, below):
            inside = [
                ln
                for ln in lines
                if ln is not line
                and overlap(ln.bbox, region) >= OVERLAP
                and not _is_label(ln.text, labels)
                and _looks_like(spec.type, ln.text)
            ]
            if inside:
                return min(inside, key=lambda ln: (ln.bbox[1] - y0) ** 2 + (ln.bbox[0] - x1) ** 2)
        return None

    candidates: list[Candidate] = []
    ocr_hit: OcrLine | None = None
    if rest and _looks_like(spec.type, rest) and not _is_label(rest, labels):
        ocr_hit = line
        candidates.append(
            Candidate("ocr", rest, line.bbox, line.confidence, _value(spec.type, rest))
        )
    else:
        ocr_hit = first_value(page.lines)
        if ocr_hit is not None:
            candidates.append(
                Candidate(
                    "ocr",
                    ocr_hit.text,
                    ocr_hit.bbox,
                    ocr_hit.confidence,
                    _value(spec.type, ocr_hit.text),
                )
            )
    layer_hit = first_value(page.text_layer) if page.text_layer else None
    if layer_hit is not None:
        candidates.append(
            Candidate(
                "text_layer",
                layer_hit.text,
                layer_hit.bbox,
                None,
                _value(spec.type, layer_hit.text),
            )
        )
    key = is_key(spec.type, spec)
    if zoom is not None and (ocr_hit is None or key):
        # OCR 什么都没读到（字太小、太淡没检测出来），或者是关键字段：放大再认一次，多一个读法
        if ocr_hit is not None and ocr_hit is not line:
            target: Sequence[float] = ocr_hit.bbox
        elif layer_hit is not None:
            target = layer_hit.bbox
        else:
            target = (x1, right[1], min(float(page.width), x1 + NEAR * height), right[3])
        zoomed = zoom(target)
        if zoomed is not None and _looks_like(spec.type, zoomed.text):
            candidates.append(replace(zoomed, value=_value(spec.type, zoomed.text)))
    found_all = distinct(candidates)
    if found_all:
        found = "、".join(f"「{c.text}」（{SOURCE_NAMES[c.source]}）" for c in found_all)
        note = f"模型没有给出「{spec.label}」；在原文「{line.text}」旁边找到 {found}，请核对后采用"
        return replace(a, candidates=found_all, needs_review=True, note=_join(a.note, note))
    if spec.required:
        note = f"模板要求填写；原文有「{line.text}」，旁边没读到值，留空待审"
        return replace(a, needs_review=True, note=_join(a.note, note))
    return a


def _find_label(spec: TemplateField, lines: Sequence[OcrLine]) -> tuple[OcrLine, str] | None:
    """原文里这个字段的标签（名称或别名）在哪一块，以及这一块里标签后面的字。

    整块就是标签（"Total :"）最好，其次是开头是标签（"Total: 9.00"），再次是包含标签；
    一样好时：名称、别名按模板里的先后（Total 先于 Amount），再取短的那块。"""
    names = [n for n in (spec.label, *spec.aliases) if n and len(_squash(n)) >= 2]
    best: tuple[tuple[int, int, int], OcrLine, str] | None = None
    for ln in lines:
        text = plain_cjk(ln.text)
        bare = _squash(re.sub(r"[\s:：*#.-]+$", "", text))
        for order, name in enumerate(names):
            wanted = _squash(name).rstrip(".:：#")
            rest = ""
            if bare == wanted:
                score = 3
            elif _squash(text).startswith(wanted):
                score = 2
                rest = _after(text, wanted)
            elif wanted in _squash(text):
                score = 1
            else:
                continue
            rank = (score, -order, -len(text))
            if best is None or rank > best[0]:
                best = (rank, ln, rest.strip())
    return None if best is None else (best[1], best[2])


def _after(text: str, wanted: str) -> str:
    """text 开头是标签 wanted（已去掉空格、小写）：返回标签后面的字（去掉冒号）。"""
    wanted = wanted.rstrip(".:：")
    seen = ""
    for i, char in enumerate(text):
        if not char.isspace():
            seen += char.lower()
        if seen == wanted:
            return text[i + 1 :].lstrip(" .:：-#")
    return ""


def _cut_at_labels(rest: str, labels: frozenset[str]) -> str:
    """同一块里后面还跟着别的标签（"08:15 接收时间：08:42"）：只要第一个标签之前的。"""
    lowered = plain_cjk(rest).lower()
    cut = len(rest)
    for label in labels:
        at = lowered.find(label)
        if 0 < at < cut and (len(label) >= 3 or not label.isascii()):
            cut = at
    return rest[:cut].strip(" ,;，；")


def _is_label(text: str, labels: frozenset[str]) -> bool:
    """这段字像是标签（"Invoice Date:"、别的字段的名称），不是值。"""
    text = plain_cjk(text).strip()
    if text.endswith((":", "：")):
        return True
    return _squash(re.sub(r"[\s:：*#.-]+$", "", text)) in labels


def _looks_like(type_: str, text: str) -> bool:
    """这段字像不像这种字段的值。"""
    text = plain_cjk(text).strip()
    if not text:
        return False
    if type_ == "number":
        # 整段基本就是一个数（可以带货币符号、单位），"AMOUNT DUE ON August 3" 这种不算
        return bool(_NUMBER_ONLY.fullmatch(text)) and parse_number(text) is not None
    if type_ == "date":
        return parse_date(text) is not None
    if type_ == "date_range":
        return parse_date_range(text).range is not None
    return bool(re.search(r"\w", text))


_NUMBER_ONLY = re.compile(
    r"(?:[A-Za-z]{0,3}\s?[$€£¥₹]|[A-Za-z]{2,3}\.?)?\s*[-+(]?\d[\d,.\s]*\)?\s*[A-Za-z%]{0,4}\.?"
)


# ---------- 3. PDF 表单字段 ----------


def _check_form(a: AlignedField, spec: TemplateField, page: OcrPage) -> AlignedField:
    form = _form_for(spec, page.forms)
    if form is None:
        return a
    key = is_key(spec.type, spec)
    if a.field.value is not None and supports(spec.type, a.field.value, form.value, key):
        return a
    shown = _visibility(form, page)
    candidate = Candidate(
        "form", form.value, form.box, None, _value(spec.type, form.value), note=shown
    )
    if a.field.value is None:
        note = f"PDF 表单字段「{form.name}」里存着「{form.value}」（{shown}），请核对后采用"
    else:
        note = (
            f"PDF 表单字段「{form.name}」存的是「{form.value}」（{shown}），"
            "和现在的值不一样，请核对"
        )
    return replace(
        a,
        candidates=distinct((*a.candidates, candidate)),
        needs_review=True,
        note=_join(a.note, note),
    )


def _form_for(spec: TemplateField, forms: Sequence[FormValue]) -> FormValue | None:
    names = {_squash(n) for n in (spec.name, spec.label, *spec.aliases) if n}
    for form in forms:
        name = _squash(form.name.split(".")[-1])
        if name in names or any(len(n) >= 2 and (n in name or name in n) for n in names if name):
            return form
    return None


def _visibility(form: FormValue, page: OcrPage) -> str:
    if form.hidden:
        return "页面不可见"
    wanted = _squash(form.value)
    for ln in page.lines:
        if overlap(ln.bbox, form.box) >= OVERLAP and wanted and wanted in _squash(ln.text):
            return "页面上能看到"
    return "可见性待确认"


# ---------- 小工具 ----------


SOURCE_NAMES = {
    "ocr": "OCR",
    "text_layer": "PDF 文字层",
    "zoom": "局部放大再识别",
    "form": "表单字段",
}


def overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """两个框重叠的面积占小的那个的多少（0～1）。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / smaller if smaller > 0 else 0.0


def _union(boxes: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _rows(lines: Sequence[OcrLine]) -> int:
    """这几段字分成几行（按中线，离得比半个字高远就算另一行）。"""
    middles = sorted((ln.bbox[1] + ln.bbox[3]) / 2 for ln in lines)
    height = min(ln.bbox[3] - ln.bbox[1] for ln in lines)
    rows = 1
    for prev, cur in pairwise(middles):
        if cur - prev > 0.5 * height:
            rows += 1
    return rows


def _value(type_: str, text: str) -> str | None:
    """候选的文字 → 按字段类型整理过的值；整理不出来是 None。"""
    text = plain_cjk(text).strip()
    if type_ == "number":
        number = parse_number(text)
        return None if number is None else _number(number)
    if type_ == "date":
        day = parse_date(text)
        return day.isoformat() if day else None
    if type_ == "date_range":
        found = parse_date_range(text).range
        return found.text(" ~ ") if found else None
    # 前面带着标签（"Seller: Example Ltd"）就去掉标签；"08:15" 这种冒号不算
    labelled = re.match(r"^[^\d:：]{1,30}[:：]\s*(.+)$", text)
    return (labelled[1] if labelled else text).strip() or None


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def distinct(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """同样的读法只留第一个（按整理过的值比，整理不出来按文字比）。"""
    seen: set[str] = set()
    kept: list[Candidate] = []
    for c in candidates:
        mark = c.value if c.value is not None else _squash(plain_cjk(c.text))
        if mark in seen:
            continue
        seen.add(mark)
        kept.append(c)
    return tuple(kept)


def _join(*notes: str) -> str:
    return "；".join(n for n in notes if n)


# ---------- 规则提示：换成哪个候选，规则能通过 ----------


def rule_hints(
    aligned: Sequence[AlignedField],
    fields: Sequence[ExtractedField],
    validator: Validator,
) -> tuple[AlignedField, ...]:
    """有候选的字段：逐个换成候选值查一遍规则，记下和这个字段有关的规则过不过。

    只是提示（例如"明细加起来等于这个合计"），不自动采用：两个不同的金额可能都过得了格式检查。
    所有候选结果都一样时（规则不管这个字段）不记。"""
    done: list[AlignedField] = []
    for a in aligned:
        if not a.candidates:
            done.append(a)
            continue
        results: list[bool | None] = []
        for c in a.candidates:
            typed = _typed(a.field.type, c.value if c.value is not None else c.text)
            if typed is None:
                results.append(None)
                continue
            trial = [replace(f, value=typed) if f.name == a.field.name else f for f in fields]
            issues = validator.validate(trial).issues
            results.append(not any(a.field.name in i.fields for i in issues))
        known = {r for r in results if r is not None}
        if len(known) < 2:
            done.append(a)
            continue
        hinted = tuple(replace(c, rule_ok=r) for c, r in zip(a.candidates, results, strict=True))
        done.append(replace(a, candidates=hinted))
    return tuple(done)


def _typed(type_: str, text: str) -> str | float | None:
    if type_ == "number":
        return parse_number(text)
    return text or None
