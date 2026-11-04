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
    """保存一个文件和它每一页的抽取结果。调用方负责提交（session_scope 会自动提交）。"""
    who = {"user_id": owner.user_id, "team_id": owner.team_id}
    document = Document(
        filename=path.name,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
        page_count=len(reports),
        **who,
    )
    session.add(document)

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
            duration_ms=e.duration_ms,
            raw_output=e.raw_output,
            checks=_checks_json(report.checks),
            **who,
        )
        session.add(extraction)
        flagged = report.checks.fields_with_issues()
        for position, field in enumerate(report.aligned):
            session.add(_value_row(field, position, extraction, field.field.name in flagged, who))

    session.flush()  # 让数据库现在就分配好 ID、检查约束，有错马上报
    return document


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
        "bbox": [round(v, 1) for v in ev.bbox],
        "confidence": round(ev.confidence, 4),
        "similarity": ev.similarity,
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
