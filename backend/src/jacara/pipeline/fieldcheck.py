"""Check each template field: the PDF text layer, form fields and zoomed re-recognition serve only
as candidates.

Evidence alignment (BlockAligner) has already located evidence in the OCR result. This module
then does three things:
1. Values with evidence: compare with the text-layer characters at the same position (by field
   type; a single differing digit counts as different). Equal → record "text layer agrees";
   ambiguous (several nearby lines) → flag key fields; different → re-recognize the zoomed
   region, keep every differing reading as a candidate, and always flag key fields for
   confirmation. The text layer never replaces the OCR value: in a PDF whose font mapping is off
   by one glyph, the text layer's 1234567898 and the image's 1234567890 have a line similarity of
   0.94, so adopting the text layer by similarity would turn a correct value into a wrong one.
2. Fields without a model value (iterating over the template, not just the model answer): look
   for the field's label in the source; text next to it that looks like a value (OCR, text layer,
   zoomed re-recognition) becomes a flagged candidate; if no label is found and the field is
   required, it is left empty for review. A missing "Total" line can never be detected by looking
   only at the model answer.
3. Values stored in PDF form fields: for form fields whose name matches a field, the value
   becomes a candidate when it differs or the model gave none; hidden form fields are marked "not
   visible on the page", and visible ones that OCR did not read are marked "visibility
   unconfirmed"."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from itertools import pairwise

from jacara.core.cjk import plain_cjk
from jacara.core.daterange import parse_date_range
from jacara.core.dates import date_pattern
from jacara.core.i18n import N_, join_items, join_notes, stored
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
"""Zoomed re-recognition: box on the tall page → candidate (None when no text is recognized)."""

KEY_TYPES = frozenset({"number", "date", "date_range"})
"""Key fields: numbers such as amounts, quantities and test results; dates and date ranges; and
fields marked "must be unique" in the template (document numbers). Conflicts on key fields always
require confirmation."""

OVERLAP = 0.5
"""A text-layer span is at the same position as the evidence box when their overlap exceeds half of
the smaller one."""

SIMILAR = 0.9
"""Non-key text fields (names, addresses): a text layer this similar to the value counts as equal
(punctuation and spacing often differ)."""

NEAR = 12
"""When looking for missed values, how many character heights to scan to the right of the label."""


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


# ---------- 1. Values with evidence: compare with the text layer ----------


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
        note = stored(
            ("PDF 文本层此处有多行文字（「{text}」），无法确定对应哪一行，无法核对"),
            text=layer_text,
        )
        return replace(
            a,
            layer="ambiguous",
            needs_review=a.needs_review or key,
            note=join_notes(a.note, _please_check(note) if key else note),
        )
    layer_box = _union([ln.bbox for ln in hits])
    candidates = [
        Candidate("ocr", ocr_text, box, a.evidence.confidence, _value(a.field.type, ocr_text)),
        Candidate("text_layer", layer_text, layer_box, None, _value(a.field.type, layer_text)),
    ]
    zoomed = zoom(_union([box, layer_box])) if zoom is not None else None
    # Reaching this point means the text layer does not contain the current value, which does not
    # necessarily mean the text layer and OCR differ: when both agree but neither matches the value,
    # say so explicitly.
    same = _squash(plain_cjk(layer_text)) == _squash(plain_cjk(ocr_text))
    if same:
        note = stored(
            "PDF 文本层与 OCR 此处均为「{text}」，与当前值「{value}」不符",
            text=layer_text,
            value=a.field.value,
        )
    else:
        note = stored(
            "PDF 文本层此处为「{text}」，与 OCR 识别的「{ocr}」不一致",
            text=layer_text,
            ocr=ocr_text,
        )
    zoom_agrees = False
    if zoomed is not None:
        zoomed = replace(zoomed, value=_value(a.field.type, zoomed.text))
        candidates.append(zoomed)
        zoom_agrees = supports(a.field.type, a.field.value, zoomed.text, key, day_first)
        layer_value = _value(a.field.type, layer_text)
        if zoom_agrees:
            verdict = stored(
                "{how}结果为「{text}」，与当前值一致", how=zoomed.note, text=zoomed.text
            )
        elif supports(a.field.type, layer_value, zoomed.text, key, day_first):
            verdict = (
                stored(
                    "{how}结果为「{text}」，与文本层和 OCR 一致", how=zoomed.note, text=zoomed.text
                )
                if same
                else stored(
                    "{how}结果为「{text}」，与文本层一致", how=zoomed.note, text=zoomed.text
                )
            )
        elif supports(a.field.type, _value(a.field.type, ocr_text), zoomed.text, key, day_first):
            verdict = stored(
                "{how}结果为「{text}」，与 OCR 一致", how=zoomed.note, text=zoomed.text
            )
        else:
            verdict = (
                stored("{how}结果为「{text}」，与前两者均不一致", how=zoomed.note, text=zoomed.text)
                if same
                else stored(
                    "{how}结果为「{text}」，三者均不一致", how=zoomed.note, text=zoomed.text
                )
            )
        note = join_notes(note, verdict)
    review = key or not zoom_agrees
    return replace(
        a,
        layer="conflict",
        candidates=distinct(candidates),
        needs_review=a.needs_review or review,
        note=join_notes(a.note, _please_check(note) if review else note),
    )


def _please_check(note: str) -> str:
    return stored("{note}，请核对", note=note)


def supports(
    type_: str, value: object, text: str, key: bool = True, day_first: bool | None = None
) -> bool:
    """Whether text contains value (compared by field type). Radical code points are normalized on
    both sides first."""
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


# ---------- 2. Fields without a model value: search by label ----------


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
            note = stored("模板要求必填，原文中未找到该字段的标签，留空待审核")
            return replace(a, needs_review=True, note=join_notes(a.note, note))
        return a
    line, rest = label
    rest = _cut_at_labels(rest, labels)
    height = max(1.0, line.bbox[3] - line.bbox[1])
    x0, y0, x1, y1 = line.bbox
    # Along the same line to the right page edge (in two-column layouts values are often
    # right-aligned); otherwise directly below the label.
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
        # OCR read nothing (text too small or faint to be detected), or a key field: re-recognize
        # zoomed for one more reading.
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
        found = join_items(
            stored("「{text}」（{source}）", text=c.text, source=stored(SOURCE_NAMES[c.source]))
            for c in found_all
        )
        note = stored(
            ("模型未给出「{label}」；在原文「{line}」附近找到 {found}，请核对后采用"),
            label=spec.label,
            line=line.text,
            found=found,
        )
        return replace(a, candidates=found_all, needs_review=True, note=join_notes(a.note, note))
    if spec.required:
        note = stored(
            ("模板要求必填；原文中有「{line}」，但其附近未读取到值，留空待审核"), line=line.text
        )
        return replace(a, needs_review=True, note=join_notes(a.note, note))
    return a


def _find_label(spec: TemplateField, lines: Sequence[OcrLine]) -> tuple[OcrLine, str] | None:
    """The source block containing this field's label (name or alias), and the text after the label
    within the block.

        A block that is exactly the label ("Total :") is best, then one starting with the label
    ("Total: 9.00"), then one containing it; ties are broken by the order of name and aliases in
    the template (Total before Amount), then by the shorter block."""
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
    """If text starts with the label wanted (whitespace removed, lowercase), return the text after
    the label (colon removed)."""
    wanted = wanted.rstrip(".:：")
    seen = ""
    for i, char in enumerate(text):
        if not char.isspace():
            seen += char.lower()
        if seen == wanted:
            return text[i + 1 :].lstrip(" .:：-#")
    return ""


def _cut_at_labels(rest: str, labels: frozenset[str]) -> str:
    """Another label follows within the same block ("08:15 接收时间：08:42"): keep only the text
    before it."""
    lowered = plain_cjk(rest).lower()
    cut = len(rest)
    for label in labels:
        at = lowered.find(label)
        if 0 < at < cut and (len(label) >= 3 or not label.isascii()):
            cut = at
    return rest[:cut].strip(" ,;，；")


def _is_label(text: str, labels: frozenset[str]) -> bool:
    """Whether the text looks like a label ("Invoice Date:", another field's name) rather than a
    value."""
    text = plain_cjk(text).strip()
    if text.endswith((":", "：")):
        return True
    return _squash(re.sub(r"[\s:：*#.-]+$", "", text)) in labels


def _looks_like(type_: str, text: str) -> bool:
    """Whether the text looks like a value of this field type."""
    text = plain_cjk(text).strip()
    if not text:
        return False
    if type_ == "number":
        # The text is essentially one number (optionally with currency symbol or unit); "AMOUNT DUE
        # ON August 3" does not qualify.
        return bool(_NUMBER_ONLY.fullmatch(text)) and parse_number(text) is not None
    if type_ == "date":
        return parse_date(text) is not None
    if type_ == "date_range":
        return parse_date_range(text).range is not None
    return bool(re.search(r"\w", text))


_NUMBER_ONLY = re.compile(
    r"(?:[A-Za-z]{0,3}\s?[$€£¥₹]|[A-Za-z]{2,3}\.?)?\s*[-+(]?\d[\d,.\s]*\)?\s*[A-Za-z%]{0,4}\.?"
)


# ---------- 3. PDF form fields ----------


def _check_form(a: AlignedField, spec: TemplateField, page: OcrPage) -> AlignedField:
    form = _form_for(spec, page.forms)
    if form is None:
        return a
    key = is_key(spec.type, spec)
    if a.field.value is not None and supports(spec.type, a.field.value, form.value, key):
        return a
    visible = _visibility(form, page)
    shown = stored(VISIBILITY[visible])
    candidate = Candidate(
        "form",
        form.value,
        form.box,
        None,
        _value(spec.type, form.value),
        note=shown,
        visible=visible,
    )
    if a.field.value is None:
        note = stored(
            ("PDF 表单字段「{name}」中存储了「{value}」（{shown}），请核对后采用"),
            name=form.name,
            value=form.value,
            shown=shown,
        )
    else:
        note = stored(
            ("PDF 表单字段「{name}」中存储的是「{value}」（{shown}），与当前值不一致，请核对"),
            name=form.name,
            value=form.value,
            shown=shown,
        )
    return replace(
        a,
        candidates=distinct((*a.candidates, candidate)),
        needs_review=True,
        note=join_notes(a.note, note),
    )


def _form_for(spec: TemplateField, forms: Sequence[FormValue]) -> FormValue | None:
    names = {_squash(n) for n in (spec.name, spec.label, *spec.aliases) if n}
    for form in forms:
        name = _squash(form.name.split(".")[-1])
        if name in names or any(len(n) >= 2 and (n in name or name in n) for n in names if name):
            return form
    return None


def _visibility(form: FormValue, page: OcrPage) -> bool | None:
    """Whether the form value is visible on the page: True when a source block at the same place
    contains it, False when the field is hidden, None when this cannot be confirmed."""
    if form.hidden:
        return False
    wanted = _squash(form.value)
    for ln in page.lines:
        if overlap(ln.bbox, form.box) >= OVERLAP and wanted and wanted in _squash(ln.text):
            return True
    return None


VISIBILITY = {True: N_("页面可见"), False: N_("页面不可见"), None: N_("可见性待确认")}
"""Note shown on a form-field candidate for each visibility state."""


# ---------- Helpers ----------


SOURCE_NAMES = {
    "ocr": "OCR",
    "text_layer": N_("PDF 文本层"),
    "zoom": N_("局部放大后重新识别"),
    "form": N_("表单字段"),
}


def overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """Overlap of two boxes as a share of the smaller one (0–1)."""
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
    """Number of lines formed by these spans (by center line; more than half a character height
    apart starts a new line)."""
    middles = sorted((ln.bbox[1] + ln.bbox[3]) / 2 for ln in lines)
    height = min(ln.bbox[3] - ln.bbox[1] for ln in lines)
    rows = 1
    for prev, cur in pairwise(middles):
        if cur - prev > 0.5 * height:
            rows += 1
    return rows


def _value(type_: str, text: str) -> str | None:
    """Candidate text → value normalized by field type; None if it cannot be normalized."""
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
    # Strip a leading label ("Seller: Example Ltd"); colons as in "08:15" do not count.
    labelled = re.match(r"^[^\d:：]{1,30}[:：]\s*(.+)$", text)
    return (labelled[1] if labelled else text).strip() or None


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def distinct(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Keep only the first of identical readings (compared by normalized value, or by text when not
    normalizable)."""
    seen: set[str] = set()
    kept: list[Candidate] = []
    for c in candidates:
        mark = c.value if c.value is not None else _squash(plain_cjk(c.text))
        if mark in seen:
            continue
        seen.add(mark)
        kept.append(c)
    return tuple(kept)


# ---------- Rule hints: which candidate makes the rules pass ----------


def rule_hints(
    aligned: Sequence[AlignedField],
    fields: Sequence[ExtractedField],
    validator: Validator,
) -> tuple[AlignedField, ...]:
    """For fields with candidates: re-run the rules with each candidate substituted and record
    whether the rules involving this field pass.

        Only a hint (for example "the line items add up to this total"); never applied
    automatically, since two different amounts may both pass format checks. Not recorded when all
    candidates give the same result (no rule involves the field)."""
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
