"""Error attribution: for each field a reviewer corrected, the pipeline stage at fault. A conclusion
is drawn only when evidence suffices; otherwise the result is "unknown".

Stages:
- ocr_misread    OCR misread the text: the reviewer's transcription differs from the OCR reading
  (OCR sample), or the source block the reviewer clicked or selected does not yield the entered
  value;
- ocr_uncovered  OCR missed the region: no OCR text inside the region the reviewer selected;
- extraction     The model chose a wrong value or missed it: the entered value is in the text
  sent to the model;
- matching       Normalization or evidence matching: the model value was actually correct but was
  dropped by evidence checks, or corrupted by formatting;
- template       Template or rules: the day/month order set by the template reversed a date;
- mixed          Evidence for several stages (for example OCR misread this block, but the correct
  value appears elsewhere in the text);
- unknown        Insufficient evidence: for example the value is not in the OCR text and was
  typed, so an OCR error cannot be told apart from a value computed by the reviewer.

Evidence comes from OCR samples, the text sent to the model in this run (model_input), evidence
in revisions, the model's original value (including dropped values) and template settings. The
rule version is stored in attributor_version; attributions are recomputed when the rules change,
except those overridden by a person."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from jacara.core.i18n import N_
from jacara.modules.documents.models import Extraction
from jacara.modules.fields.models import ExtractedValue
from jacara.modules.insights.kinds import error_kind
from jacara.modules.ocr_samples.crop import lines_in_box
from jacara.modules.ocr_samples.models import OcrSample
from jacara.pipeline.fieldcheck import supports
from jacara.pipeline.pages import ocr_page_json, pages_json
from jacara.templates import Template

ATTRIBUTOR_VERSION = "attr-v1"

STAGES = {
    "ocr_misread": N_("OCR 识别错误"),
    "ocr_uncovered": N_("OCR 漏识别"),
    "extraction": N_("模型抽取错误或遗漏"),
    "matching": N_("标准化或出处匹配问题"),
    "template": N_("模板或规则"),
    "mixed": N_("多个环节有问题"),
    "unknown": N_("证据不足"),
}

REASONS = {
    "rejected_but_right": N_("模型输出的值正确，但出处核对未找到，已被丢弃"),
    "format_only": N_("模型输出的值含义相同，仅写法不同"),
    "sample_rec_error": N_("人工转写与 OCR 识别结果不一致（OCR 样本）"),
    "sample_missed": N_("人工框选区域 OCR 未识别出文字（OCR 样本）"),
    "picked_text_differs": N_("人工点选的原文，OCR 识别结果与人工填写的值不一致"),
    "drawn_box_text_differs": N_("人工框选的区域，OCR 当时的识别结果与人工填写的值不一致"),
    "drawn_box_no_text": N_("人工框选的区域，OCR 当时未识别出任何文字"),
    "missed_in_text": N_("人工填写的值包含在发送给模型的文字中，但模型未抽取"),
    "wrong_in_text": N_("人工填写的值包含在发送给模型的文字中，但模型抽取了其他值"),
    "day_first_rule": N_("模板设定的日月顺序与此单据不一致，导致日期颠倒"),
    "day_month_swapped": N_("模型将日期的日与月颠倒，原文中存在正确写法"),
    "several": N_("多个环节均有证据，详见下方"),
    "short_value": N_("值过短（一位数或单个字符），在文字中找到也可能是巧合，不下结论"),
    "not_in_text": N_(
        "人工填写的值不在 OCR 文字中，且无样本或框选，无法区分是 OCR 遗漏还是人工计算所得"
    ),
    "no_evidence": N_("没有足以下结论的证据"),
}


@dataclass(frozen=True)
class Verdict:
    stage: str
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _sent_text(extraction: Extraction) -> tuple[str, str]:
    """Document text sent to the model and its origin: sent (recorded in this run) or ocr (documents
    processed before the input was recorded; the stored OCR text is used instead, since the whole
    page text was sent then)."""
    data = extraction.model_input
    if data and isinstance(data.get("text"), str):
        return data["text"], "sent"
    lines = [
        str(line.get("text", ""))
        for _, page in pages_json(extraction.ocr, extraction.page)
        for line in page.get("lines", [])
    ]
    return "\n".join(lines), "ocr"


def _short(type_: str, value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if type_ == "number":
        return len(digits.lstrip("0")) <= 1
    if type_ in ("date", "date_range"):
        return False
    return len("".join(value.split())) <= 1


def _found_lines(type_: str, value: str, text: str, day_first: bool | None) -> list[str] | None:
    """Lines of text containing value; an empty list when it appears only across lines; None when
    absent."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hit = [ln.strip() for ln in lines if supports(type_, value, ln, day_first=day_first)]
    if hit:
        return hit[:3]
    return [] if supports(type_, value, text, day_first=day_first) else None


def _ocr_page_lines(extraction: Extraction, page: int) -> list[dict[str, Any]]:
    data = ocr_page_json(extraction.ocr, page, extraction.page)
    return list((data or {}).get("lines", []))


def attribute(
    value: ExtractedValue,
    extraction: Extraction,
    samples: Sequence[OcrSample] = (),
    template: Template | None = None,
) -> Verdict:
    """The stage at fault for one field a reviewer changed (value.current_value differs from the
    model value)."""
    truth = value.current_value
    model = value.value if value.value is not None else value.rejected_value
    type_ = value.type
    day_first = template.day_first if template is not None else None
    if truth is None or not truth.strip():
        # The reviewer cleared the value: the model gave a spurious value. Whether it exists in the
        # source is unclear; check only whether the model value appears in the text.
        return Verdict("unknown", "no_evidence")

    from jacara.eval.replay import same_value

    # 1. The model value was actually correct
    if model is not None and same_value(type_, model, truth):
        if value.value is None:
            return Verdict(
                "matching",
                "rejected_but_right",
                [{"kind": "value", "status": value.status, "rejected_value": value.rejected_value}],
            )
        return Verdict("matching", "format_only", [{"kind": "value", "value": value.value}])

    found: list[Verdict] = []
    # 2. OCR sample (transcribed by the reviewer on the page image)
    for s in samples:
        ref = {
            "kind": "ocr_sample",
            "id": str(s.id),
            "category": s.category,
            "ocr_text": s.ocr_text,
            "transcription": s.transcription,
        }
        if s.category == "rec_error":
            found.append(Verdict("ocr_misread", "sample_rec_error", [ref]))
        elif s.category == "missed":
            found.append(Verdict("ocr_uncovered", "sample_missed", [ref]))

    # 3. Evidence in revisions: the source text clicked or the region selected by the reviewer
    edits = [r for r in value.revisions if r.action == "edit"]
    last = edits[-1] if edits else None
    ev = last.evidence if last is not None else None
    if last is not None and ev and not samples:
        page = int(ev.get("page") or extraction.page)
        if ev.get("drawn"):
            inside = lines_in_box(_ocr_page_lines(extraction, page), ev["bbox"])
            text = " ".join(str(ln.get("text", "")) for ln in inside)
            ref = {"kind": "revision", "id": str(last.id), "drawn": True, "ocr_text": text}
            if not inside:
                found.append(Verdict("ocr_uncovered", "drawn_box_no_text", [ref]))
            elif not supports(type_, truth, text, day_first=day_first):
                found.append(Verdict("ocr_misread", "drawn_box_text_differs", [ref]))
        elif isinstance(ev.get("text"), str) and not supports(
            type_, truth, ev["text"], day_first=day_first
        ):
            ref = {"kind": "revision", "id": str(last.id), "ocr_text": ev["text"]}
            found.append(Verdict("ocr_misread", "picked_text_differs", [ref]))

    # 4. Day and month reversed
    if type_ == "date" and model is not None and error_kind(type_, model, truth) == "day_month":
        if day_first is not None:
            found.append(
                Verdict(
                    "template", "day_first_rule", [{"kind": "template", "day_first": day_first}]
                )
            )
        else:
            found.append(Verdict("extraction", "day_month_swapped", []))

    # 5. Whether the entered value appears in the text sent to the model
    text, source = _sent_text(extraction)
    # Day/month reversal was already handled.
    if not any(v.stage in ("extraction", "template") for v in found):
        if _short(type_, truth):
            if not found:
                return Verdict("unknown", "short_value")
        else:
            lines = _found_lines(type_, truth, text, day_first)
            if lines is not None:
                blocks = sorted({int(n) for ln in lines for n in re.findall(r"⟨(\d+)⟩", ln)})
                reason = "missed_in_text" if model is None else "wrong_in_text"
                ref = {"kind": "sent_text", "source": source, "lines": lines, "blocks": blocks}
                found.append(Verdict("extraction", reason, [ref]))

    stages = {v.stage for v in found}
    if not stages:
        return Verdict("unknown", "not_in_text" if text.strip() else "no_evidence")
    if len(stages) == 1:
        return Verdict(found[0].stage, found[0].reason, [e for v in found for e in v.evidence])
    evidence = [
        {**e, "stage": v.stage, "reason": v.reason} for v in found for e in v.evidence or [{}]
    ]
    return Verdict("mixed", "several", evidence)
