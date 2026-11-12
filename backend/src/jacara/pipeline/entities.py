"""对象在不同单据之间对不上（ADR 0031）。

模板里有对象（例如病人：编号 + 姓名、性别、出生日期）时，同一个编号在别的单据上也出现过，
就比一比属于这个对象的其他字段：写得不一样，标黄提醒（不知道哪份是对的，所以两边都要人看）。
只看同一个模板、当前这一轮、同一个团队的文档，最多比 50 份。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.models import Document, ExtractedValue, Extraction
from jacara.templates import Template
from jacara.templates.extras import normalize

ENTITY_CONFLICT = "entity_conflict"
MAX_OTHERS = 50


def _current(extraction: Extraction) -> dict[str, str]:
    return {
        v.name: v.current_value
        for v in extraction.values
        if not v.deleted and v.current_value not in (None, "")
    }


def entity_conflicts(
    session: Session, extraction: Extraction, template: Template
) -> list[dict[str, Any]]:
    """返回和 current_checks 里 issues 一样格式的提醒。"""
    if not template.entities:
        return []
    mine = _current(extraction)
    labels = template.labels()
    issues: list[dict[str, Any]] = []
    for entity in template.entities:
        key = mine.get(entity.key)
        if key is None:
            continue
        others = session.scalars(
            select(Extraction)
            .join(Document, Extraction.document_id == Document.id)
            .join(ExtractedValue, ExtractedValue.extraction_id == Extraction.id)
            .where(
                Extraction.team_id == extraction.team_id,
                Extraction.template == extraction.template,
                Extraction.run == Document.current_run,
                Extraction.document_id != extraction.document_id,
                ExtractedValue.name == entity.key,
            )
            .options(
                selectinload(Extraction.values).selectinload(ExtractedValue.revisions),
                selectinload(Extraction.document),
            )
            .order_by(Extraction.created_at.desc())
            .limit(MAX_OTHERS * 4)
        ).unique()
        matched = [
            (o, values)
            for o in others
            if normalize(str((values := _current(o)).get(entity.key, ""))) == normalize(key)
        ][:MAX_OTHERS]
        for name in entity.fields:
            if name == entity.key or name not in mine:
                continue
            different = {
                o.document.filename: values[name]
                for o, values in matched
                if name in values and normalize(values[name]) != normalize(mine[name])
            }
            if not different:
                continue
            shown = "；".join(f"《{f}》写的是「{v}」" for f, v in list(different.items())[:3])
            more = f" 等 {len(different)} 份" if len(different) > 3 else ""
            issues.append(
                {
                    "rule": ENTITY_CONFLICT,
                    "title": f"{entity.label}：和别的单据不一致",
                    "message": f"同一个{labels.get(entity.key, entity.key)}「{key}」，"
                    f"{shown}{more}，这里是「{mine[name]}」",
                    "fields": [name],
                    "severity": "warning",
                }
            )
    return issues
