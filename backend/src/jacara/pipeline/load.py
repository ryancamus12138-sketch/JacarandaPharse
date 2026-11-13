"""从数据库读回 DocumentReport：store.py 的反方向。

导出器是插件，不碰数据库（项目规则 7），只认 DocumentReport。
数据库里存的是文档、抽取、字段、审核记录几张表，这里把它们拼回导出器认识的样子，
再加上每个字段最新的人工审核结果（reviews）。
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.ranges import range_settings
from jacara.plugins.aligners.base import AlignedField, AlignStatus, Evidence
from jacara.plugins.exporters.base import DocumentReport, Review
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult, FieldType
from jacara.plugins.ocr.base import OcrLine, Point
from jacara.plugins.validators.base import Issue, ValidationResult
from jacara.templates import Template


def load_reports(document: Document, language: str | None = None) -> list[DocumentReport]:
    """一个文档的每张单据 → 一份 DocumentReport。要先把抽取、字段、审核记录都查出来。
    language：导出时字段名（列名）用哪种语言（ADR 0048），不写是中文。"""
    from jacara.pipeline.current import page_range

    several = len(document.extractions) > 1
    return [
        _report(
            extraction,
            f"{document.filename} {page_range(extraction)}" if several else document.filename,
            language,
        )
        for extraction in document.extractions
    ]


def _report(extraction: Extraction, source: str, language: str | None = None) -> DocumentReport:
    """当前的结果：删掉的行不导出，表格按显示顺序编号，规则按审核之后的值重新检查。"""
    from jacara.pipeline.current import current_checks, current_values, labels_of, template_of

    current = current_values(extraction)
    fields = tuple(_field(v, name) for v, name in current)
    result = ExtractionResult(
        fields=fields,
        raw_output=extraction.raw_output,
        model=extraction.model,
        backend=extraction.backend,
        prompt_version=extraction.prompt_version,
        duration_ms=extraction.duration_ms,
    )
    aligned = tuple(
        AlignedField(
            field=f,
            # cast：告诉类型检查"数据库里存的就是这几个值之一"，运行时什么都不做
            status=cast(AlignStatus, v.status),
            evidence=_evidence(v.current_evidence),
            needs_review=v.needs_review,
            note=v.note,
        )
        for f, (v, _) in zip(fields, current, strict=True)
    )
    reviews = {
        name: Review(
            action=v.revisions[-1].action,
            value=v.revisions[-1].new_value,
            reason=v.revisions[-1].reason,
            reviewed_at=v.revisions[-1].created_at,
        )
        for v, name in current
        if v.revisions
    }
    return DocumentReport(
        source=source,
        extraction=result,
        aligned=aligned,
        checks=current_checks(extraction),
        ocr_engine=extraction.ocr_engine,
        processed_at=extraction.created_at,
        page=extraction.page,
        last_page=extraction.last_page,
        text_source=extraction.source,
        template=extraction.template,
        template_version=extraction.template_version,
        reviews=reviews,
        labels=labels_of(extraction, language),
        hidden=_hidden(template_of(extraction)),
        ranges=range_settings(template_of(extraction)),
    )


def _hidden(template: Template | None) -> frozenset[str]:
    """模板里设成不导出的字段、表、表的列。"""
    if template is None:
        return frozenset()
    names = {f.name for f in template.fields if not f.export}
    for t in template.tables:
        if not t.export:
            names.add(t.name)
        names |= {f"{t.name}.{c.name}" for c in t.columns if not c.export}
    return frozenset(names)


def _field(value: ExtractedValue, name: str) -> ExtractedField:
    """数据库里的值都是文字；数字还原成数字，规则和导出才能按数字处理。

    人工新增的行没有模型原值，原值就是空的；导出时用审核之后的值。
    """
    raw: str | float | None = value.value
    if value.type == "number" and raw is not None:
        with suppress(ValueError):  # 转不成数字就保持原样（规则校验会标出来）
            raw = float(raw)
    quote = (value.evidence or {}).get("quote")
    return ExtractedField(
        name,
        cast(FieldType, value.type),
        raw,
        value.row_no,
        value.label,
        text=quote if isinstance(quote, str) else None,
    )


def _evidence(data: dict[str, Any] | None) -> Evidence | None:
    """出处只存了合并后的框和最低置信度，还原成一个"文字块"，bbox 和 confidence 和原来一样。"""
    if data is None:
        return None
    x0, y0, x1, y1 = data["bbox"]
    line = OcrLine(
        text=data["row_text"],
        polygon=(Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1)),
        confidence=data["confidence"],
    )
    return Evidence(data.get("row") or 0, data["row_text"], (line,), data["similarity"])


def stored_checks(data: dict[str, Any]) -> ValidationResult:
    return ValidationResult(
        issues=tuple(
            Issue(i["rule"], i["title"], i["message"], tuple(i["fields"]), i["severity"])
            for i in data.get("issues", [])
        ),
        passed=tuple(data.get("passed", [])),
        skipped=tuple(data.get("skipped", [])),
    )
