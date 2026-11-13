"""从人改过的地方学（ADR 0045）：抽取时把同一个模板以前人工改过的例子告诉模型。

不训练模型，只在提示词里多一段"以前容易错在哪"：
- 只用审核完成的单据、现在那一轮、模型给的值被人改过的字段；
- 敏感字段（姓名、病历号、电话……）不用：别人的个人信息不放进另一张单据的提示词；
- 不用这张单据自己（重新处理时）；
- 按"改得多的字段优先、每个字段最多 2 个最近的例子、一共最多 8 条"挑；
- 例子只说明容易错在哪，明确告诉模型"这张单据的值要按它自己的原文"。
提示词版本记成 extract-v8+lessonsN（N 是用了几条），能追溯。
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.modules.insights.kinds import KIND_NAMES, error_kind

LIMIT = 8
"""一次最多给几条例子：太多会占上下文，也会让模型照抄。"""
PER_FIELD = 2
SCAN = 200
"""最多看最近多少张审核完成的单据。"""

NOTE = """以前人工审核同一种单据时改过下面这些地方（是别的单据上的例子，只说明容易错在哪里；
这张单据的值一律按它自己的原文，不要照抄例子里的值）：
{lines}"""


@dataclass(frozen=True)
class Lesson:
    field: str
    label: str
    before: str | None
    after: str | None
    kind: str


def lesson_line(lesson: Lesson) -> str:
    if lesson.kind == "missing":
        return f"- {lesson.label}：模型没有给，原文其实有（例如「{lesson.after}」），要仔细找"
    if lesson.kind == "extra":
        return f"- {lesson.label}：模型给了「{lesson.before}」，原文其实没有，应该给 null"
    reason = KIND_NAMES.get(lesson.kind, "")
    return f"- {lesson.label}：模型写成「{lesson.before}」，人改成「{lesson.after}」（{reason}）"


def pick(lessons: Iterable[Lesson], limit: int = LIMIT, per_field: int = PER_FIELD) -> list[Lesson]:
    """改得多的字段优先，每个字段最多 per_field 条（lessons 按新到旧排好）。"""
    lessons = list(lessons)
    counts = Counter(lesson.field for lesson in lessons)
    kept: list[Lesson] = []
    used: Counter[str] = Counter()
    for field, _ in counts.most_common():
        for lesson in lessons:
            if lesson.field == field and used[field] < per_field:
                kept.append(lesson)
                used[field] += 1
        if len(kept) >= limit:
            break
    return kept[:limit]


def note_for(lessons: Sequence[Lesson]) -> str:
    """几条例子 → 提示词里的一段。没有例子是空字符串。"""
    if not lessons:
        return ""
    return NOTE.format(lines="\n".join(lesson_line(lesson) for lesson in lessons))


def _generic(name: str) -> str:
    from jacara.plugins.extractors.base import split_cell_name

    cell = split_cell_name(name)
    return f"{cell[0]}.{cell[2]}" if cell else name


def from_database(
    session: Session, team_id: uuid.UUID, template: str, exclude: uuid.UUID | None = None
) -> list[Lesson]:
    """这个团队、这个模板（extractions.template）审核完成的单据里，人改过的地方，新的在前。"""
    from jacara.modules.documents.models import Document, Extraction
    from jacara.modules.fields.models import ExtractedValue
    from jacara.pipeline.current import label_for, labels_of, template_of

    query = (
        select(Document)
        .where(Document.team_id == team_id, Document.status == "reviewed")
        .options(
            selectinload(Document.extractions)
            .selectinload(Extraction.values)
            .selectinload(ExtractedValue.revisions)
        )
        .order_by(Document.reviewed_at.desc())
        .limit(SCAN)
    )
    if exclude is not None:
        query = query.where(Document.id != exclude)
    found: list[tuple[object, Lesson]] = []
    for document in session.scalars(query):
        for extraction in document.extractions:
            if extraction.template != template:
                continue
            tpl = template_of(extraction)
            hidden = _sensitive(tpl)
            labels = labels_of(extraction)
            for value in extraction.values:
                if value.origin != "model" or value.deleted or not value.revisions:
                    continue
                field = _generic(value.name)
                if field in hidden:
                    continue
                before, after = value.value, value.current_value
                if (before or "").strip() == (after or "").strip():
                    continue
                label = (label_for(labels, value.name) or value.label or value.name).split(" · ")[0]
                lesson = Lesson(field, label, before, after, error_kind(value.type, before, after))
                found.append((value.revisions[-1].created_at, lesson))
    found.sort(key=lambda item: item[0], reverse=True)  # type: ignore[arg-type, return-value]
    return [lesson for _, lesson in found]


def _sensitive(template: object) -> set[str]:
    if template is None:
        return set()
    names = {f.name for f in template.fields if f.sensitive}  # type: ignore[attr-defined]
    for table in template.tables:  # type: ignore[attr-defined]
        names |= {f"{table.name}.{c.name}" for c in table.columns if c.sensitive}
    return names


def from_dataset(path: Path) -> dict[str, list[tuple[str, Lesson]]]:
    """导出的审核数据（train.jsonl，ADR 0044）→ 模板 → [(文档 id, 例子)]。成对测评时用，
    同一张单据的例子不给它自己（留一法）。"""
    by_template: dict[str, list[tuple[str, Lesson]]] = {}
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        for change in entry.get("corrections", []):
            field = _generic(change["field"])
            lesson = Lesson(field, field, change.get("before"), change.get("after"), change["kind"])
            by_template.setdefault(entry["template"], []).append((entry["document"], lesson))
    return by_template
