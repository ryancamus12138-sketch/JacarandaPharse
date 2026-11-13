"""从人改过的地方学（ADR 0045）：抽取时把同一个模板以前人工改过的例子告诉模型。

不训练模型，只在提示词里多一段"以前容易错在哪"：
- 只用审核完成的单据、现在那一轮、模型给的值被人改过的字段；
- 敏感字段（姓名、病历号、电话……）不用：别人的个人信息不放进另一张单据的提示词；
- 不用这张单据自己（重新处理时）；
- 按"改得多的字段优先、每个字段最多 2 个最近的例子、一共最多 8 条"挑；
- 例子只说明容易错在哪，明确告诉模型"这张单据的值要按它自己的原文"。
提示词版本记成 extract-v8+lessonsN（N 是用了几条），能追溯。

整理过的例子（ADR 0055，B2 + B3，`JACARA_LEARN_MODE=curated`，默认还是上面的 recent）：
- 只用错在模型这一步的修改（错误归因 B1）：OCR 认错、OCR 没认出、模型其实对了被核对丢掉的，
  告诉模型也没用，不当例子；
- 同一字段、同一种错合成一条，记出现过几次，次数多的、新的在前；
- 字段在模板新版本里改了类型或说明的，旧例子不用（过期）；
- 同一个发单方（系列）审核完成满 3 份的，先用这个系列自己的例子，不够再用同模板的；
- 句子用固定句式按错误类型写，不让模型自己总结（本地小模型总结规则不可靠）。
是否比 recent 好，用 jacara eval replay 在锁定测试集上比过再切换。
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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
    series: str | None = None
    """这条例子来自哪个系列（发单方，ADR 0055）。"""
    stage: str | None = None
    """错在哪一步（错误归因，人改判过的用人改的）。"""
    sig: str | None = None
    """当时这个字段在模板里的定义（类型、说明），模板改了就过期。"""
    document: str | None = None
    count: int = 1
    """整理时合并了几次一样的修改。"""


MODES = ("recent", "curated")
MIN_SERIES = 3
"""一个系列审核完成满几份，才单独用它的例子（照 ABBYY：至少 3 份）。"""
SKIP_STAGES = frozenset({"ocr_misread", "ocr_uncovered", "matching", "mixed"})
"""这几种错不在模型：告诉模型也改不了，不当例子。"""


def field_sig(template: object, field: str) -> str | None:
    """模板里这个字段（表格的列写成 items.amount）现在的定义：类型 + 说明。找不到是 None。"""
    if template is None:
        return None
    for f in template.fields:  # type: ignore[attr-defined]
        if f.name == field:
            return f"{f.type}|{f.description}"
    table, _, column = field.partition(".")
    for t in template.tables:  # type: ignore[attr-defined]
        if t.name == table:
            for c in t.columns:
                if c.name == column:
                    return f"{c.type}|{c.description}"
    return None


def curate(
    lessons: Iterable[Lesson],
    text: str = "",
    template: object = None,
    limit: int = LIMIT,
    per_field: int = PER_FIELD,
) -> list[Lesson]:
    """整理过的例子（ADR 0055）。lessons 按新到旧排好；text 是这张单据的 OCR 文字（认系列用）；
    template 是这次用的模板（去掉过期的）。"""
    from jacara.pipeline.series import series_in

    usable = [
        lesson
        for lesson in lessons
        if lesson.stage not in SKIP_STAGES
        and (
            template is None
            or lesson.sig is None
            or lesson.sig == field_sig(template, lesson.field)
        )
    ]
    # 系列：满 MIN_SERIES 份的才算，再看这张单据的文字里写没写它
    docs: dict[str, set[str | None]] = {}
    for lesson in usable:
        if lesson.series:
            docs.setdefault(lesson.series, set()).add(lesson.document)
    known = [key for key, found in docs.items() if len(found) >= MIN_SERIES]
    mine = series_in(text, known) if text and known else None
    # 合并：同一字段、同一种错合成一条，留最新的那个例子，记次数
    merged: dict[tuple[bool, str, str], Lesson] = {}
    order: list[tuple[bool, str, str]] = []
    for lesson in usable:
        key = (lesson.series == mine and mine is not None, lesson.field, lesson.kind)
        if key in merged:
            merged[key] = replace(merged[key], count=merged[key].count + 1)
        else:
            merged[key] = lesson
            order.append(key)
    ranked = sorted(order, key=lambda k: (not k[0], -merged[k].count, order.index(k)))
    kept: list[Lesson] = []
    used: Counter[str] = Counter()
    for key in ranked:
        lesson = merged[key]
        if used[lesson.field] >= per_field:
            continue
        kept.append(lesson)
        used[lesson.field] += 1
        if len(kept) >= limit:
            break
    return kept


def lesson_line(lesson: Lesson) -> str:
    times = f"（同样的错出现过 {lesson.count} 次）" if lesson.count > 1 else ""
    if lesson.kind == "missing":
        return (
            f"- {lesson.label}：模型没有给，原文其实有（例如「{lesson.after}」），要仔细找{times}"
        )
    if lesson.kind == "extra":
        return f"- {lesson.label}：模型给了「{lesson.before}」，原文其实没有，应该给 null{times}"
    reason = KIND_NAMES.get(lesson.kind, "")
    line = f"- {lesson.label}：模型写成「{lesson.before}」，人改成「{lesson.after}」（{reason}）"
    return line + times


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
    from jacara.modules.ocr_samples.groups import locked_units
    from jacara.pipeline.series import series_key

    locked = locked_units(session, team_id)  # 锁定测试集的单据不当例子来源（ADR 0052）
    edited: list[tuple[ExtractedValue, Extraction, object, str, str, str | None]] = []
    for document in session.scalars(query):
        for extraction in document.extractions:
            if extraction.template != template or (document.id, extraction.page) in locked:
                continue
            tpl = template_of(extraction)
            hidden = _sensitive(tpl)
            labels = labels_of(extraction)
            series = series_key(
                {v.name: v.current_value for v in extraction.values if not v.deleted}
            )
            for value in extraction.values:
                if value.origin != "model" or value.deleted or not value.revisions:
                    continue
                field = _generic(value.name)
                if field in hidden:
                    continue
                if (value.value or "").strip() == (value.current_value or "").strip():
                    continue
                label = (label_for(labels, value.name) or value.label or value.name).split(" · ")[0]
                edited.append((value, extraction, tpl, field, label, series))
    stages = _stages(session, [item[:3] for item in edited])
    found: list[tuple[object, Lesson]] = []
    for value, extraction, tpl, field, label, series in edited:
        before, after = value.value, value.current_value
        lesson = Lesson(
            field,
            label,
            before,
            after,
            error_kind(value.type, before, after),
            series=series,
            stage=stages.get(value.id),
            sig=field_sig(tpl, field),
            document=str(extraction.document_id),
        )
        found.append((value.revisions[-1].created_at, lesson))
    found.sort(key=lambda item: item[0], reverse=True)  # type: ignore[arg-type, return-value]
    return [lesson for _, lesson in found]


def _stages(session: Session, items: Sequence[tuple[Any, Any, Any]]) -> dict[uuid.UUID, str]:
    """每个改过的字段错在哪一步（ADR 0053）：人改判过的用人改的，没有就按证据现判（不写数据库）。"""
    from jacara.modules.insights.attribution import attribute
    from jacara.modules.insights.models import ErrorAttribution
    from jacara.modules.ocr_samples.models import OcrSample

    ids = [value.id for value, _, _ in items]
    if not ids:
        return {}
    overridden = {
        a.value_id: a.override_stage
        for a in session.scalars(
            select(ErrorAttribution).where(
                ErrorAttribution.value_id.in_(ids), ErrorAttribution.override_stage.is_not(None)
            )
        )
        if a.override_stage
    }
    samples: dict[uuid.UUID, list[Any]] = {}
    for s in session.scalars(
        select(OcrSample).where(OcrSample.value_id.in_(ids), OcrSample.status == "ready")
    ):
        if s.value_id is not None:
            samples.setdefault(s.value_id, []).append(s)
    stages: dict[uuid.UUID, str] = {}
    for value, extraction, tpl in items:
        if value.id in overridden:
            stages[value.id] = overridden[value.id]
            continue
        revision = value.revisions[-1].id
        mine = [s for s in samples.get(value.id, []) if s.revision_id == revision]
        stages[value.id] = attribute(value, extraction, mine, tpl).stage
    return stages


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
