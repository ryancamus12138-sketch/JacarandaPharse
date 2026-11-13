"""错误归因（ADR 0053，B1）：人改过的字段，错在哪一步。只在证据够的时候下结论，不够就说"不确定"。

阶段：
- ocr_misread    OCR 认错了字：人在原图上转写的和 OCR 读的不一样（OCR 样本），
                 或者人点的那块原文 / 框住的那块原文读不出人填的值；
- ocr_uncovered  OCR 没认出这块：人框的地方 OCR 一个字都没有；
- extraction     模型取错或漏取：人填的值就在发给模型的文字里；
- matching       标准化或出处匹配：模型给的其实是对的，被出处核对丢掉了，或者格式换坏了；
- template       模板或规则：模板定的日月顺序让日期读反了；
- mixed          几处都有证据（例如 OCR 在这一块认错了，但别处的文字里有对的值）；
- unknown        证据不够：例如值不在 OCR 文字里，人又是手打的，分不清是 OCR 的问题还是人算出来的。

证据来自：OCR 样本（ADR 0050）、这一轮发给模型的文字（model_input）、审核记录里的出处、
模型给的原值（被丢掉的也记着）、模板设置。规则的版本记在 attributor_version，规则改了重新判断，
人改判过的不动。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

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
    "ocr_misread": "OCR 认错了字",
    "ocr_uncovered": "OCR 没认出这块",
    "extraction": "模型取错或漏取",
    "matching": "标准化或出处匹配",
    "template": "模板或规则",
    "mixed": "几处都有问题",
    "unknown": "证据不够",
}

REASONS = {
    "rejected_but_right": "模型给的值是对的，但出处核对没找到，被丢掉了",
    "format_only": "模型给的值意思一样，只是写法不同",
    "sample_rec_error": "人在原图上转写的和 OCR 读的不一样（OCR 样本）",
    "sample_missed": "人框的地方 OCR 没有认出文字（OCR 样本）",
    "picked_text_differs": "人点的那块原文，OCR 读出来不是人填的值",
    "drawn_box_text_differs": "人框住的那块，OCR 当时读出来不是人填的值",
    "drawn_box_no_text": "人框的地方 OCR 当时一个字都没有",
    "missed_in_text": "人填的值在发给模型的文字里，模型没取",
    "wrong_in_text": "人填的值在发给模型的文字里，模型取了别的",
    "day_first_rule": "模板定的日月顺序和这张单据不一样，日期读反了",
    "day_month_swapped": "模型把日期的日和月读反了，原文里有对的写法",
    "several": "几处都有证据，见下面",
    "short_value": "值太短（一位数、一个字），在文字里找到也可能是碰巧，不下结论",
    "not_in_text": "人填的值不在 OCR 文字里，也没有样本或框，分不清是 OCR 漏了还是人算出来的",
    "no_evidence": "没有能下结论的证据",
}


@dataclass(frozen=True)
class Verdict:
    stage: str
    reason: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _sent_text(extraction: Extraction) -> tuple[str, str]:
    """发给模型的单据文字和它是怎么来的：sent（这一轮记下的），ocr（更新前处理的，
    用存下的 OCR 文字代替：那时整页文字都会发给模型）。"""
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
    """value 在 text 里的哪几行；整段里有、单独一行没有时返回空列表；没有是 None。"""
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
    """一个人改过的字段（value.current_value 和模型给的不一样）错在哪一步。"""
    truth = value.current_value
    model = value.value if value.value is not None else value.rejected_value
    type_ = value.type
    day_first = template.day_first if template is not None else None
    if truth is None or not truth.strip():
        # 人把值清空了：模型多给了一个值。原文里有没有不好说，只看模型给的值在不在文字里
        return Verdict("unknown", "no_evidence")

    from jacara.eval.replay import same_value

    # 1. 模型给的其实是对的
    if model is not None and same_value(type_, model, truth):
        if value.value is None:
            return Verdict(
                "matching",
                "rejected_but_right",
                [{"kind": "value", "status": value.status, "rejected_value": value.rejected_value}],
            )
        return Verdict("matching", "format_only", [{"kind": "value", "value": value.value}])

    found: list[Verdict] = []
    # 2. OCR 样本（人在原图上转写过）
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

    # 3. 审核记录里的出处：人点的原文、人框的地方
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

    # 4. 日月读反了
    if type_ == "date" and model is not None and error_kind(type_, model, truth) == "day_month":
        if day_first is not None:
            found.append(
                Verdict(
                    "template", "day_first_rule", [{"kind": "template", "day_first": day_first}]
                )
            )
        else:
            found.append(Verdict("extraction", "day_month_swapped", []))

    # 5. 人填的值在不在发给模型的文字里
    text, source = _sent_text(extraction)
    if not any(v.stage in ("extraction", "template") for v in found):  # 日月读反的已经判过了
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
