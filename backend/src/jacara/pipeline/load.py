"""从数据库读回 DocumentReport：store.py 的反方向。

导出器是插件，不碰数据库（项目规则 7），只认 DocumentReport。
数据库里存的是文档、抽取、字段、审核记录几张表，这里把它们拼回导出器认识的样子，
再加上每个字段最新的人工审核结果（reviews）。
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

from jacara.models import Document, ExtractedValue, Extraction
from jacara.plugins.aligners.base import AlignedField, AlignStatus, Evidence
from jacara.plugins.exporters.base import DocumentReport, Review
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult, FieldType
from jacara.plugins.ocr.base import OcrLine, Point
from jacara.plugins.validators.base import Issue, ValidationResult


def load_reports(document: Document) -> list[DocumentReport]:
    """一个文档的每一页 → 一份 DocumentReport。要先把抽取、字段、审核记录都查出来。"""
    pages = len(document.extractions)
    return [
        _report(extraction, _source(document.filename, extraction.page, pages))
        for extraction in document.extractions
    ]


def _source(filename: str, page: int, pages: int) -> str:
    return f"{filename} 第 {page} 页" if pages > 1 else filename


def _report(extraction: Extraction, source: str) -> DocumentReport:
    fields = tuple(_field(v) for v in extraction.values)
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
            evidence=_evidence(v.evidence),
            needs_review=v.needs_review,
            note=v.note,
        )
        for f, v in zip(fields, extraction.values, strict=True)
    )
    reviews = {
        v.name: Review(
            action=v.revisions[-1].action,
            value=v.revisions[-1].new_value,
            reason=v.revisions[-1].reason,
            reviewed_at=v.revisions[-1].created_at,
        )
        for v in extraction.values
        if v.revisions
    }
    return DocumentReport(
        source=source,
        extraction=result,
        aligned=aligned,
        checks=_checks(extraction.checks),
        ocr_engine=extraction.ocr_engine,
        processed_at=extraction.created_at,
        page=extraction.page,
        text_source=extraction.source,
        template=extraction.template,
        reviews=reviews,
    )


def _field(value: ExtractedValue) -> ExtractedField:
    """数据库里的值都是文字；数字还原成数字，规则和导出才能按数字处理。"""
    raw: str | float | None = value.value
    if value.type == "number" and raw is not None:
        with suppress(ValueError):  # 转不成数字就保持原样（规则校验会标出来）
            raw = float(raw)
    return ExtractedField(value.name, cast(FieldType, value.type), raw, value.row_no)


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
    return Evidence(data["row"], data["row_text"], (line,), data["similarity"])


def _checks(data: dict[str, Any]) -> ValidationResult:
    return ValidationResult(
        issues=tuple(
            Issue(i["rule"], i["title"], i["message"], tuple(i["fields"]), i["severity"])
            for i in data.get("issues", [])
        ),
        passed=tuple(data.get("passed", [])),
        skipped=tuple(data.get("skipped", [])),
    )
