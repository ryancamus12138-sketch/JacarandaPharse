"""OCR 样本按业务单据分组、划分训练集和验证集（ADR 0052）。

- 业务单据 = 文件 + 这张单据的起始页；同一组的样本永远在同一边。
- 一组第一次进数据集时按 SHA-256(种子:版本:组号) 分桶，前 20% 进验证集，结果存表，之后只读表。
- 字节一样的文件自动同组；PDF 和照片按单号、日期、金额……提名，人确认后合并。
- 合并、拆开时如果已经导出过，组被隔离，受影响的导出记录记一笔。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from jacara.modules.documents.models import Document, Extraction
from jacara.modules.ocr_samples.models import (
    DocGroupMember,
    OcrSample,
    OcrSampleExport,
    PairDecision,
    SplitAssignment,
    TeamSetting,
)

BUCKETS = 10_000
VAL_BUCKETS = 2_000
"""0～9999 里前 2000 个桶进验证集：按组的目标比例 80/20。"""

SPLITS = ("train", "val", "test_locked", "quarantined")
SPLIT_NAMES = {
    "train": "训练集",
    "val": "验证集",
    "test_locked": "锁定测试集",
    "quarantined": "隔离",
}

Unit = tuple[uuid.UUID, int]
"""业务单据：（文档 id，这张单据的起始页）。"""


# ---- 种子和版本 ----


def _setting(session: Session, team_id: uuid.UUID, key: str, make: Any) -> Any:
    found = session.get(TeamSetting, (team_id, key))
    if found is None:
        found = TeamSetting(team_id=team_id, key=key, value=make())
        session.add(found)
        session.flush()
    return found.value


def split_seed(session: Session, team_id: uuid.UUID) -> str:
    """这个团队划分用的随机种子（第一次用时生成，之后不变）。"""
    return str(_setting(session, team_id, "ocr_split_seed", lambda: secrets.token_hex(16)))


def split_version(session: Session, team_id: uuid.UUID) -> int:
    return int(_setting(session, team_id, "ocr_split_version", lambda: 1))


def bucket(seed: str, version: int, group_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"{seed}:{version}:{group_id}".encode()).hexdigest()
    return int(digest, 16) % BUCKETS


def hashed_split(seed: str, version: int, group_id: uuid.UUID) -> str:
    return "val" if bucket(seed, version, group_id) < VAL_BUCKETS else "train"


def anonymous(seed: str, group_id: uuid.UUID) -> str:
    """导出清单里的组号：稳定（多次导出一样）、不含业务信息。"""
    return "g_" + hashlib.sha256(f"{seed}:group:{group_id}".encode()).hexdigest()[:12]


# ---- 组 ----


def member_of(session: Session, unit: Unit) -> DocGroupMember | None:
    document_id, first_page = unit
    return session.scalar(
        select(DocGroupMember).where(
            DocGroupMember.document_id == document_id, DocGroupMember.first_page == first_page
        )
    )


def ensure_member(
    session: Session, team_id: uuid.UUID, user_id: uuid.UUID, unit: Unit
) -> DocGroupMember:
    """业务单据的组：没有就建。字节一样的文件（重复上传）同一起始页直接进同一组。"""
    found = member_of(session, unit)
    if found is not None:
        return found
    document_id, first_page = unit
    document = session.get_one(Document, document_id)
    twin = session.scalar(
        select(DocGroupMember)
        .join(Document, DocGroupMember.document_id == Document.id)
        .where(
            Document.team_id == team_id,
            Document.sha256 == document.sha256,
            Document.id != document_id,
            DocGroupMember.first_page == first_page,
        )
    )
    member = DocGroupMember(
        group_id=twin.group_id if twin is not None else uuid.uuid4(),
        document_id=document_id,
        first_page=first_page,
        user_id=user_id,
        team_id=team_id,
    )
    session.add(member)
    session.flush()
    ensure_split(session, team_id, member.group_id)
    return member


def assignment(
    session: Session, team_id: uuid.UUID, group_id: uuid.UUID, version: int | None = None
) -> SplitAssignment | None:
    version = version if version is not None else split_version(session, team_id)
    return session.get(SplitAssignment, (team_id, version, group_id))


def ensure_split(session: Session, team_id: uuid.UUID, group_id: uuid.UUID) -> SplitAssignment:
    """这一组在当前划分版本里分在哪边：第一次按哈希分好存下，之后只读表。"""
    version = split_version(session, team_id)
    found = assignment(session, team_id, group_id, version)
    if found is None:
        found = SplitAssignment(
            team_id=team_id,
            version=version,
            group_id=group_id,
            split=hashed_split(split_seed(session, team_id), version, group_id),
            reason="第一次进数据集时按哈希分",
        )
        session.add(found)
        session.flush()
    return found


def ensure_sample_groups(session: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """还没分组的样本（ADR 0052 以前收集的）补上：起始页按它来自的那张单据，再分组、分边。"""
    from jacara.modules.fields.models import ExtractedValue

    grouped = select(DocGroupMember.document_id, DocGroupMember.first_page)
    samples = session.scalars(select(OcrSample).where(OcrSample.team_id == team_id)).all()
    have = {(d, p) for d, p in session.execute(grouped.where(DocGroupMember.team_id == team_id))}
    for sample in samples:
        if (sample.document_id, sample.unit_page) in have:
            continue
        value = session.get(ExtractedValue, sample.value_id) if sample.value_id else None
        if value is not None:
            sample.unit_page = value.extraction.page
        unit = (sample.document_id, sample.unit_page)
        ensure_member(session, team_id, user_id, unit)
        have.add(unit)


def groups_of(session: Session, team_id: uuid.UUID) -> dict[Unit, uuid.UUID]:
    rows = session.execute(
        select(
            DocGroupMember.document_id, DocGroupMember.first_page, DocGroupMember.group_id
        ).where(DocGroupMember.team_id == team_id)
    )
    return {(d, p): g for d, p, g in rows}


def splits_of(session: Session, team_id: uuid.UUID) -> dict[uuid.UUID, SplitAssignment]:
    version = split_version(session, team_id)
    rows = session.scalars(
        select(SplitAssignment).where(
            SplitAssignment.team_id == team_id, SplitAssignment.version == version
        )
    )
    return {a.group_id: a for a in rows}


def locked_units(session: Session, team_id: uuid.UUID) -> set[Unit]:
    """锁定测试集里的业务单据：不当"从人改过的地方学"的例子来源（ADR 0052）。"""
    splits = splits_of(session, team_id)
    return {
        unit
        for unit, group in groups_of(session, team_id).items()
        if (a := splits.get(group)) is not None and a.split == "test_locked"
    }


# ---- 导出过没有 ----


def exports_with(
    session: Session, team_id: uuid.UUID, groups: Iterable[uuid.UUID]
) -> list[OcrSampleExport]:
    """导出过这几组里任何一组的导出记录。"""
    wanted = {str(g) for g in groups}
    records = session.scalars(
        select(OcrSampleExport).where(OcrSampleExport.team_id == team_id)
    ).all()
    return [r for r in records if any(item.get("group") in wanted for item in r.items or [])]


def exported_as(session: Session, team_id: uuid.UUID, group_id: uuid.UUID) -> set[str]:
    """这一组被导出过在哪几边（train、val、test_locked）。"""
    return {
        str(item.get("split"))
        for record in exports_with(session, team_id, [group_id])
        for item in record.items or []
        if item.get("group") == str(group_id)
    }


def _mark_affected(
    session: Session, team_id: uuid.UUID, groups: Iterable[uuid.UUID], reason: str
) -> int:
    groups = list(groups)
    records = exports_with(session, team_id, groups)
    seed = split_seed(session, team_id)
    at = datetime.now(UTC).isoformat(timespec="seconds")
    for record in records:
        present = {item.get("group") for item in record.items or []}
        for group in groups:
            if str(group) in present:
                record.affected = [
                    *(record.affected or []),
                    {"group": anonymous(seed, group), "reason": reason, "at": at},
                ]
        flag_modified(record, "affected")
    return len(records)


# ---- 合并、拆开、改划分 ----


class GroupError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class MergeResult:
    group_id: uuid.UUID
    split: str
    quarantined: bool
    affected_exports: int


def merge(
    session: Session, team_id: uuid.UUID, user_id: uuid.UUID, keep: Unit, other: Unit
) -> MergeResult:
    """确认两张业务单据是同一张：other 那一组的单据都并进 keep 那一组。

    两边划分一样照旧；不一样、两边都没导出过，按 keep 那边（有一边是锁定测试集的，整组锁定）；
    不一样、有一边已经导出过：可能一份进了训练、一份进了验证，整组隔离，受影响的导出记一笔。
    """
    a = ensure_member(session, team_id, user_id, keep)
    b = ensure_member(session, team_id, user_id, other)
    first, second = a.group_id, b.group_id
    split_a = ensure_split(session, team_id, first)
    if first == second:
        return MergeResult(first, split_a.split, split_a.split == "quarantined", 0)
    split_b = ensure_split(session, team_id, second)
    touched = bool(exports_with(session, team_id, [first, second]))
    for member in session.scalars(
        select(DocGroupMember).where(DocGroupMember.group_id == second)
    ).all():
        member.group_id = first
    affected = 0
    splits = {split_a.split, split_b.split}
    if "quarantined" in splits:
        split_a.split, split_a.reason = "quarantined", "合并的两组里有一组本来就被隔离"
    elif len(splits) == 1:
        pass
    elif not touched:
        if "test_locked" in splits:
            split_a.split, split_a.reason = "test_locked", "合并：有一组是锁定测试集"
        else:
            split_a.reason = f"合并：按保留那一组（{SPLIT_NAMES[split_a.split]}）"
    else:
        reason = (
            f"确认是同一张单据时，两组已经导出过、分在不同边"
            f"（{SPLIT_NAMES[split_a.split]}、{SPLIT_NAMES[split_b.split]}）：可能一份训练、一份验证"
        )
        split_a.split, split_a.reason = "quarantined", reason
        affected = _mark_affected(session, team_id, [first, second], reason)
    session.delete(split_b)
    session.flush()
    return MergeResult(first, split_a.split, split_a.split == "quarantined", affected)


def detach(session: Session, team_id: uuid.UUID, member_id: uuid.UUID) -> MergeResult:
    """配错了：把这张单据从组里拆出来，换一个新组。原来的组导出过的，两边都隔离。"""
    member = session.get(DocGroupMember, member_id)
    if member is None or member.team_id != team_id:
        raise GroupError("not_found", "找不到这张单据")
    old = member.group_id
    rest = session.scalars(
        select(DocGroupMember).where(DocGroupMember.group_id == old, DocGroupMember.id != member.id)
    ).all()
    if not rest:
        raise GroupError("invalid", "这一组只有这一张单据，不用拆")
    member.group_id = uuid.uuid4()
    session.flush()
    new_split = ensure_split(session, team_id, member.group_id)
    affected = 0
    if exports_with(session, team_id, [old]):
        reason = "拆开配错的单据时，原来的组已经导出过：拆出来的这张可能在另一边也出现过"
        new_split.split, new_split.reason = "quarantined", reason
        old_split = ensure_split(session, team_id, old)
        old_split.split, old_split.reason = "quarantined", reason
        affected = _mark_affected(session, team_id, [old], reason)
    session.flush()
    return MergeResult(member.group_id, new_split.split, new_split.split == "quarantined", affected)


def set_split(
    session: Session, team_id: uuid.UUID, group_id: uuid.UUID, split: str
) -> SplitAssignment:
    """管理员手动改一组的划分。

    导出进过训练集的不能再当验证集或测试集；锁定测试集只能锁从没导出过的组（要独立、干净）。
    """
    if split not in SPLITS:
        raise GroupError("invalid", f"没有这种划分：{split}")
    if not session.scalar(
        select(DocGroupMember.id).where(
            DocGroupMember.group_id == group_id, DocGroupMember.team_id == team_id
        )
    ):
        raise GroupError("not_found", "找不到这一组")
    found = ensure_split(session, team_id, group_id)
    if split == found.split:
        return found
    used = exported_as(session, team_id, group_id)
    if split in ("val", "test_locked") and "train" in used:
        raise GroupError("invalid", "这一组已经导出进训练集，不能再当验证集或测试集")
    if split == "test_locked" and used - {"test_locked"}:
        raise GroupError("invalid", "锁定测试集只能用从没导出过的组（测试集要独立、没人看过）")
    if found.split == "val" and split == "train" and "val" in used:
        _mark_affected(
            session,
            team_id,
            [group_id],
            "这一组从验证集改到训练集：之前用它算的验证成绩以后不再独立",
        )
    found.split = split
    found.reason = "管理员手动改的"
    session.flush()
    return found


# ---- PDF 和照片配对：提名 ----

TOTAL_NAMES = ("total", "amount", "total_amount", "grand_total", "amount_due", "total_with_tax")
DATE_NAMES = ("date", "issue_date", "invoice_date", "bill_date", "report_date")
ISSUER_NAMES = (
    "merchant",
    "seller",
    "seller_name",
    "issuer",
    "store_name",
    "vendor",
    "supplier",
    "company",
)


@dataclass(frozen=True)
class UnitKey:
    """配对用的几个值：单据类型、单号、日期、金额、币种、发单方。"""

    unit: Unit
    kind: str
    numbers: tuple[tuple[str, str], ...]
    date: str | None
    total: float | None
    currency: str | None
    issuer: str | None
    shown: dict[str, str]


def _same(text: str | None) -> str:
    return "".join((text or "").split()).upper()


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def unit_key(extraction: Extraction) -> UnitKey:
    from jacara.pipeline.current import current_values, template_of
    from jacara.plugins.extractors.base import split_cell_name

    template = template_of(extraction)
    unique = {f.name for f in template.fields if f.unique} if template is not None else set()
    values = {
        name: (v.current_value or "").strip()
        for v, name in current_values(extraction)
        if split_cell_name(name) is None and (v.current_value or "").strip()
    }
    types = {v.name: v.type for v in extraction.values}
    kind = (
        str(extraction.template_def.template_id)
        if extraction.template_def is not None
        else extraction.template
    )
    date_name = next((n for n in DATE_NAMES if n in values), None) or next(
        (n for n in values if types.get(n) == "date"), None
    )
    total_name = next((n for n in TOTAL_NAMES if n in values), None)
    issuer_name = next((n for n in ISSUER_NAMES if n in values), None)
    numbers = tuple(sorted((n, _same(values[n])) for n in unique if n in values))
    shown = {
        **{n: values[n] for n in unique if n in values},
        **({date_name: values[date_name]} if date_name else {}),
        **({total_name: values[total_name]} if total_name else {}),
        **({issuer_name: values[issuer_name]} if issuer_name else {}),
        **({"currency": values["currency"]} if "currency" in values else {}),
    }
    return UnitKey(
        unit=(extraction.document_id, extraction.page),
        kind=kind,
        numbers=numbers,
        date=values.get(date_name) if date_name else None,
        total=_float(values.get(total_name)) if total_name else None,
        currency=_same(values.get("currency")) or None,
        issuer=_same(values.get(issuer_name)) if issuer_name else None,
        shown=shown,
    )


def _agree(a: object, b: object) -> bool:
    """两边都有才比；有一边没有不算冲突。"""
    if a is None or b is None:
        return True
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 0.005
    return a == b


def same_document(a: UnitKey, b: UnitKey) -> bool:
    """像不像同一张单据（只是提名，人确认后才合并）。只有日期和金额一样不算。"""
    if a.kind != b.kind:
        return False
    if not all(
        _agree(x, y)
        for x, y in (
            (a.date, b.date),
            (a.total, b.total),
            (a.currency, b.currency),
            (a.issuer, b.issuer),
        )
    ):
        return False
    numbers_a, numbers_b = dict(a.numbers), dict(b.numbers)
    shared = set(numbers_a) & set(numbers_b)
    if shared:
        return all(numbers_a[n] == numbers_b[n] for n in shared)
    if numbers_a or numbers_b:
        return False  # 一边有单号、一边没有：对不上
    return None not in (a.issuer, b.issuer, a.date, b.date, a.total, b.total)


def nominations(session: Session, team_id: uuid.UUID) -> list[tuple[UnitKey, UnitKey]]:
    """可能是同一张单据、还没人确认过的两张（至少一张已经有样本）。"""
    members = groups_of(session, team_id)
    if not members:
        return []
    documents = session.scalars(
        select(Document)
        .where(Document.team_id == team_id, Document.status.in_(("done", "reviewed")))
        .options(selectinload(Document.extractions).selectinload(Extraction.values))
    ).all()
    sha = {d.id: d.sha256 for d in documents}
    keys = [unit_key(e) for d in documents for e in d.extractions]
    decided = {
        frozenset({(r.a_document, r.a_page), (r.b_document, r.b_page)})
        for r in session.scalars(select(PairDecision).where(PairDecision.team_id == team_id))
    }
    by_kind: dict[str, list[UnitKey]] = defaultdict(list)
    for key in keys:
        by_kind[key.kind].append(key)
    found: list[tuple[UnitKey, UnitKey]] = []
    for group in by_kind.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if a.unit[0] == b.unit[0] or sha.get(a.unit[0]) == sha.get(b.unit[0]):
                    continue  # 同一个文件、或者字节一样（已经自动同组）
                if a.unit not in members and b.unit not in members:
                    continue  # 两边都还没样本：等有了再说
                if members.get(a.unit) is not None and members.get(a.unit) == members.get(b.unit):
                    continue
                if frozenset({a.unit, b.unit}) in decided:
                    continue
                if same_document(a, b):
                    found.append((a, b) if a.unit in members else (b, a))
    return found
