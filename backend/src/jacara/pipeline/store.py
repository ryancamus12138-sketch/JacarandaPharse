"""把流水线的结果存进数据库。

插件不碰数据库（项目规则 7）：插件只返回 DocumentReport，由这里负责保存。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.models import Document, ExtractedValue, Extraction
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
        page_count=len(reports),
        status="done",
        user_id=owner.user_id,
        team_id=owner.team_id,
    )
    session.add(document)
    add_extractions(session, document, reports)
    return document


def add_extractions(
    session: Session, document: Document, reports: Sequence[DocumentReport]
) -> None:
    """把每一页的抽取结果挂到一个已有的文档上。后台任务处理上传的文件时用这个。

    抽取结果的归属和文档一样（同一个 user、同一个 team）。
    """
    who = {"user_id": document.user_id, "team_id": document.team_id}
    for report in reports:
        e = report.extraction
        extraction = Extraction(
            document=document,
            page=report.page,
            source=report.text_source,
            ocr_engine=report.ocr_engine,
            model=e.model,
            backend=e.backend,
            prompt_version=e.prompt_version,
            template=report.template,
            template_version=report.template_version,
            duration_ms=e.duration_ms,
            raw_output=e.raw_output,
            checks=_checks_json(report.checks),
            ocr=ocr_json(report.ocr_page),
            **who,
        )
        session.add(extraction)
        flagged = report.checks.fields_with_issues()
        for position, field in enumerate(report.aligned):
            session.add(_value_row(field, position, extraction, field.field.name in flagged, who))

    session.flush()  # 让数据库现在就分配好 ID、检查约束，有错马上报


def _value_row(
    field: AlignedField,
    position: int,
    extraction: Extraction,
    rule_failed: bool,
    who: dict[str, Any],
) -> ExtractedValue:
    value = field.field.value
    return ExtractedValue(
        extraction=extraction,
        position=position,
        sort_key=float(position),
        name=field.field.name,
        type=field.field.type,
        value=None if value is None else str(value),
        row_no=field.field.row,
        status=field.status,
        needs_review=field.needs_review or rule_failed,
        note=field.note,
        evidence=_evidence_json(field),
        **who,
    )


def _evidence_json(field: AlignedField) -> dict[str, Any] | None:
    ev = field.evidence
    if ev is None:
        return None
    return {
        "row": ev.row,
        "row_text": ev.row_text,
        "text": " ".join(line.text for line in ev.lines),
        "bbox": [round(v, 1) for v in ev.bbox],
        "confidence": round(ev.confidence, 4),
        "similarity": ev.similarity,
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


def _checks_json(checks: ValidationResult) -> dict[str, Any]:
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
