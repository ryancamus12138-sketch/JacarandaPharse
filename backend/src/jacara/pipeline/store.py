"""把流水线的结果存进数据库。

插件不碰数据库（项目规则 7）：插件只返回 DocumentReport，由这里负责保存。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.pages import local_box
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters.base import DocumentReport
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.validators.base import ValidationResult


def file_sha256(path: Path) -> str:
    """文件内容的指纹。文件可能很大，所以一块一块地读。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_document(
    session: Session,
    path: Path,
    reports: Sequence[DocumentReport],
    owner: Owner = PERSONAL_OWNER,
) -> Document:
    """命令行用：新建一条文档记录，连同每一页的抽取结果一起保存。调用方负责提交。"""
    document = Document(
        filename=path.name,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
        page_count=page_count(reports),
        status="done",
        user_id=owner.user_id,
        team_id=owner.team_id,
    )
    session.add(document)
    add_extractions(session, document, reports)
    return document


def page_count(reports: Sequence[DocumentReport]) -> int:
    """文件有几页：最后一张单据的最后一页（一张单据可能占好几页，ADR 0036）。"""
    return max((r.last_page or r.page for r in reports), default=0)


def add_extractions(
    session: Session,
    document: Document,
    reports: Sequence[DocumentReport],
    *,
    run: int = 1,
    template_version_id: uuid.UUID | None = None,
    run_reason: str | None = None,
) -> None:
    """把每一页的抽取结果挂到一个已有的文档上。后台任务处理上传的文件时用这个。

    抽取结果的归属和文档一样（同一个 user、同一个 team）。run 是第几轮处理（ADR 0030）。
    """
    who = {"user_id": document.user_id, "team_id": document.team_id}
    for report in reports:
        e = report.extraction
        extraction = Extraction(
            document=document,
            page=report.page,
            last_page=report.last_page,
            source=report.text_source,
            ocr_engine=report.ocr_engine,
            model=e.model,
            backend=e.backend,
            prompt_version=e.prompt_version,
            template=report.template,
            template_version=report.template_version,
            # 自动判断的（ADR 0038）每张单据各用各的模板；没有自动判断就用文档上选的
            template_version_id=report.template_version_id
            if report.classified is not None
            else template_version_id,
            classified=dict(report.classified) if report.classified is not None else None,
            vision=e.vision,
            run=run,
            run_reason=run_reason,
            duration_ms=e.duration_ms,
            raw_output=e.raw_output,
            checks=checks_json(report.checks),
            ocr=report_ocr_json(report),
            structure=report.structure.to_json() if report.structure else None,
            **who,
        )
        session.add(extraction)
        flagged = report.checks.fields_with_issues()
        for position, field in enumerate(report.aligned):
            row = _value_row(field, position, extraction, field.field.name in flagged, who)
            row.evidence = _evidence_json(field, report)
            if field.field.name in report.manual:
                row.origin = "manual"  # 人工填的：不是模型说"原文没有"，是等人填
                row.needs_review = False
            elif field.field.name in report.computed:
                row.origin = "computed"  # 按公式算的（ADR 0035）
            session.add(row)

    session.flush()  # 让数据库现在就分配好 ID、检查约束，有错马上报


def _value_row(
    field: AlignedField,
    position: int,
    extraction: Extraction,
    rule_failed: bool,
    who: dict[str, Any],
) -> ExtractedValue:
    value = field.field.value
    rejected = field.status == "rejected" and value is not None
    return ExtractedValue(
        extraction=extraction,
        position=position,
        sort_key=float(position),
        name=field.field.name,
        label=field.field.label,
        type=field.field.type,
        # 核对不上、按规则没有填的（ADR 0034）：值存成空的，模型给的值另外记下
        value=None if value is None or rejected else str(value),
        rejected_value=str(value) if rejected else None,
        row_no=field.field.row,
        status=field.status,
        needs_review=field.needs_review or rule_failed,
        note=field.note,
        **who,
    )


def _evidence_json(field: AlignedField, report: DocumentReport) -> dict[str, Any] | None:
    """出处：哪一页（文件里的页码）、这一页上的框。几页拼成的单据（ADR 0036），
    拼成的长页上的框换回那一页自己的坐标；框跨两页时只框上面那页的部分。"""
    ev = field.evidence
    if ev is None:
        return None
    page, bbox = report.page, list(ev.bbox)
    lines = ev.lines
    if report.page_starts:
        index, bbox = local_box(ev.bbox, report.page_starts)
        page = report.page + index
        same = [ln for ln in lines if local_box(ln.bbox, report.page_starts)[0] == index]
        if same and len(same) < len(lines):
            boxes = [local_box(ln.bbox, report.page_starts)[1] for ln in same]
            bbox = [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ]
    return {
        "page": page,
        "row": ev.row,
        "blocks": list(field.field.blocks),
        "row_text": ev.row_text,
        "text": " ".join(line.text for line in lines),
        "bbox": [round(v, 1) for v in bbox],
        "confidence": round(ev.confidence, 4),
        "similarity": ev.similarity,
        **({"quote": field.field.text} if field.field.text else {}),
    }


def report_ocr_json(report: DocumentReport) -> dict[str, Any] | None:
    """一张单据的 OCR 结果：只占一页的照旧存这一页；占好几页的，每一页存自己的
    （{"pages": [{"page": 3, "width", "height", "lines"}, …]}，坐标是那一页自己的，ADR 0036）。"""
    if not report.pages:
        return ocr_json(report.ocr_page)
    return {
        "pages": [
            {"page": report.page + k, "engine": p.engine, **(ocr_json(p) or {})}
            for k, p in enumerate(report.pages)
        ]
    }


def ocr_json(page: OcrPage | None) -> dict[str, Any] | None:
    """一页 OCR 结果存成 JSON：每个文字块的文字、外接框、四个角、置信度，还有它在第几个视觉行。

    审核页面"点原文填值"要用：点哪一块，就用哪一块的文字和位置。
    """
    if page is None:
        return None
    from jacara.plugins.ocr.layout import group_rows

    row_of = {id(line): row.index for row in group_rows(page) for line in row.lines}
    return {
        "width": page.width,
        "height": page.height,
        **({"note": page.models["note"]} if page.models.get("note") else {}),
        "lines": [
            {
                "text": line.text,
                "box": [round(v, 1) for v in line.bbox],
                "polygon": [[round(p.x, 1), round(p.y, 1)] for p in line.polygon],
                "confidence": round(line.confidence, 4),
                "row": row_of.get(id(line)),
            }
            for line in page.lines
        ],
    }


def page_from_json(data: dict[str, Any], engine: str) -> OcrPage:
    """ocr_json 的反方向：从数据库里存的 OCR 结果还原一页，不用重新识别（试跑用）。"""
    from jacara.plugins.ocr.base import OcrLine, Point

    lines = tuple(
        OcrLine(
            text=line["text"],
            polygon=cast(
                tuple[Point, Point, Point, Point], tuple(Point(x, y) for x, y in line["polygon"])
            ),
            confidence=line["confidence"],
        )
        for line in data.get("lines", [])
        if len(line.get("polygon", [])) == 4
    )
    return OcrPage(
        width=int(data.get("width") or 0),
        height=int(data.get("height") or 0),
        lines=lines,
        engine=engine,
        models={"engine": engine},
        duration_ms=0,
    )


def evidence_from_ocr(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """几个文字块（ocr_json 里的格式）→ 出处，格式和抽取时找到的出处一样。"""
    boxes = [line["box"] for line in lines]
    rows = [line["row"] for line in lines if line.get("row") is not None]
    return {
        "row": min(rows) if rows else None,
        "row_text": "  ".join(line["text"] for line in lines),
        "text": " ".join(line["text"] for line in lines),
        "bbox": [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ],
        "confidence": min(line["confidence"] for line in lines),
        "similarity": 1.0,
    }


def checks_json(checks: ValidationResult) -> dict[str, Any]:
    return {
        "passed": list(checks.passed),
        "skipped": list(checks.skipped),
        "issues": [
            {
                "rule": i.rule,
                "title": i.title,
                "message": i.message,
                "fields": list(i.fields),
                "severity": i.severity,
            }
            for i in checks.issues
        ],
    }
