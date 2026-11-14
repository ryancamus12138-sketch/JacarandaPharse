"""Group OCR samples by business document and split them into training and validation sets.

- Business document = file + first page of the document; samples of one group always land in the
  same split.
- When a group first enters a dataset it is bucketed by SHA-256(seed:version:group id); the first
  20% go to validation. The result is stored and only read afterwards.
- Files with identical bytes are grouped automatically; a PDF and a photo are proposed as a pair
  by document number, date, amount, …, and merged after a person confirms.
- Merging or splitting a group that has already been exported quarantines the group and records
  the affected exports."""

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

from jacara.core.i18n import N_, nested, stored, tr
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
"""The first 2000 of 10000 buckets go to validation: a target ratio of 80/20 by group."""

SPLITS = ("train", "val", "test_locked", "quarantined")
SPLIT_NAMES = {
    "train": N_("训练集"),
    "val": N_("验证集"),
    "test_locked": N_("锁定测试集"),
    "quarantined": N_("隔离"),
}

Unit = tuple[uuid.UUID, int]
"""Business document: (document id, first page of the document)."""


# ---- Seed and version ----


def _setting(session: Session, team_id: uuid.UUID, key: str, make: Any) -> Any:
    found = session.get(TeamSetting, (team_id, key))
    if found is None:
        found = TeamSetting(team_id=team_id, key=key, value=make())
        session.add(found)
        session.flush()
    return found.value


def split_seed(session: Session, team_id: uuid.UUID) -> str:
    """Random seed of this team's split (generated on first use, then fixed)."""
    return str(_setting(session, team_id, "ocr_split_seed", lambda: secrets.token_hex(16)))


def split_version(session: Session, team_id: uuid.UUID) -> int:
    return int(_setting(session, team_id, "ocr_split_version", lambda: 1))


def bucket(seed: str, version: int, group_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"{seed}:{version}:{group_id}".encode()).hexdigest()
    return int(digest, 16) % BUCKETS


def hashed_split(seed: str, version: int, group_id: uuid.UUID) -> str:
    return "val" if bucket(seed, version, group_id) < VAL_BUCKETS else "train"


def anonymous(seed: str, group_id: uuid.UUID) -> str:
    """Group id used in export manifests: stable across exports and free of business data."""
    return "g_" + hashlib.sha256(f"{seed}:group:{group_id}".encode()).hexdigest()[:12]


# ---- Groups ----


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
    """Group of a business document, created if missing. Files with identical bytes (duplicate
    uploads) with the same first page join the same group."""
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
    """Split of this group in the current split version: assigned by hash and stored the first time,
    then read from the table."""
    version = split_version(session, team_id)
    found = assignment(session, team_id, group_id, version)
    if found is None:
        found = SplitAssignment(
            team_id=team_id,
            version=version,
            group_id=group_id,
            split=hashed_split(split_seed(session, team_id), version, group_id),
            reason=stored("首次进入数据集时按哈希分配"),
        )
        session.add(found)
        session.flush()
    return found


def ensure_sample_groups(session: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Backfill groups for samples collected before grouping existed: the first page comes from the
    document of the sample, then group and split are assigned."""
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
    """Business documents in the locked test split: never used as sources of learning examples."""
    splits = splits_of(session, team_id)
    return {
        unit
        for unit, group in groups_of(session, team_id).items()
        if (a := splits.get(group)) is not None and a.split == "test_locked"
    }


# ---- Export status ----


def exports_with(
    session: Session, team_id: uuid.UUID, groups: Iterable[uuid.UUID]
) -> list[OcrSampleExport]:
    """Export records that included any of these groups."""
    wanted = {str(g) for g in groups}
    records = session.scalars(
        select(OcrSampleExport).where(OcrSampleExport.team_id == team_id)
    ).all()
    return [r for r in records if any(item.get("group") in wanted for item in r.items or [])]


def exported_as(session: Session, team_id: uuid.UUID, group_id: uuid.UUID) -> set[str]:
    """Splits in which this group has been exported (train, val, test_locked)."""
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


# ---- Merge, split, reassign ----


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
    """Confirm that two business documents are the same: documents in other's group are merged into
    keep's group.

        If both splits agree, nothing changes. If they differ and neither was exported, keep's
    split wins (if either is the locked test split, the whole group is locked). If they differ
    and either was exported, one copy may be in training and the other in validation: the whole
    group is quarantined and the affected exports are recorded."""
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
        split_a.split, split_a.reason = "quarantined", stored("合并的两个分组中有一个已被隔离")
    elif len(splits) == 1:
        pass
    elif not touched:
        if "test_locked" in splits:
            split_a.split, split_a.reason = (
                "test_locked",
                stored("合并：其中一个分组属于锁定测试集"),
            )
        else:
            split_a.reason = stored(
                "合并：沿用保留分组的划分（{split}）", split=nested(SPLIT_NAMES[split_a.split])
            )
    else:
        reason = stored(
            (
                "确认为同一张单据时，两个分组均已导出且位于不同划分（{first}"
                "、{second}）：可能同时出现在训练集和验证集中"
            ),
            first=nested(SPLIT_NAMES[split_a.split]),
            second=nested(SPLIT_NAMES[split_b.split]),
        )
        split_a.split, split_a.reason = "quarantined", reason
        affected = _mark_affected(session, team_id, [first, second], reason)
    session.delete(split_b)
    session.flush()
    return MergeResult(first, split_a.split, split_a.split == "quarantined", affected)


def detach(session: Session, team_id: uuid.UUID, member_id: uuid.UUID) -> MergeResult:
    """Mismatched pair: move this document out of its group into a new group. If the original group
    was exported, both are quarantined."""
    member = session.get(DocGroupMember, member_id)
    if member is None or member.team_id != team_id:
        raise GroupError("not_found", tr("单据不存在"))
    old = member.group_id
    rest = session.scalars(
        select(DocGroupMember).where(DocGroupMember.group_id == old, DocGroupMember.id != member.id)
    ).all()
    if not rest:
        raise GroupError("invalid", tr("此分组仅含这一张单据，无需拆分"))
    member.group_id = uuid.uuid4()
    session.flush()
    new_split = ensure_split(session, team_id, member.group_id)
    affected = 0
    if exports_with(session, team_id, [old]):
        reason = stored("拆分配对错误的单据时，原分组已导出：拆出的单据可能已出现在另一划分中")
        new_split.split, new_split.reason = "quarantined", reason
        old_split = ensure_split(session, team_id, old)
        old_split.split, old_split.reason = "quarantined", reason
        affected = _mark_affected(session, team_id, [old], reason)
    session.flush()
    return MergeResult(member.group_id, new_split.split, new_split.split == "quarantined", affected)


def set_split(
    session: Session, team_id: uuid.UUID, group_id: uuid.UUID, split: str
) -> SplitAssignment:
    """Manually change the split of a group (administrators).

        Groups exported into training can no longer become validation or test; only groups never
    exported can be locked as test (it must stay independent and clean)."""
    if split not in SPLITS:
        raise GroupError("invalid", tr("划分不存在：{split}", split=split))
    if not session.scalar(
        select(DocGroupMember.id).where(
            DocGroupMember.group_id == group_id, DocGroupMember.team_id == team_id
        )
    ):
        raise GroupError("not_found", tr("分组不存在"))
    found = ensure_split(session, team_id, group_id)
    if split == found.split:
        return found
    used = exported_as(session, team_id, group_id)
    if split in ("val", "test_locked") and "train" in used:
        raise GroupError("invalid", tr("此分组已导出到训练集，不能再用作验证集或测试集"))
    if split == "test_locked" and used - {"test_locked"}:
        raise GroupError(
            "invalid", tr("锁定测试集只能使用从未导出的分组（测试集需保持独立且未被查看）")
        )
    if found.split == "val" and split == "train" and "val" in used:
        _mark_affected(
            session,
            team_id,
            [group_id],
            stored("此分组从验证集改为训练集：此前基于其计算的验证结果不再独立"),
        )
    found.split = split
    found.reason = stored("管理员手动修改")
    session.flush()
    return found


# ---- Pairing PDFs and photos: proposals ----

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
    """Values used for pairing: document type, document number, date, amount, currency, issuer."""

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
    """Compare only when both sides have a value; a value missing on one side is not a conflict."""
    if a is None or b is None:
        return True
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 0.005
    return a == b


def same_document(a: UnitKey, b: UnitKey) -> bool:
    """Whether two documents look like the same one (only a proposal; merged after confirmation).
    Matching date and amount alone are not enough."""
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
        return False  # Document number on one side only: no match.
    return None not in (a.issuer, b.issuer, a.date, b.date, a.total, b.total)


def nominations(session: Session, team_id: uuid.UUID) -> list[tuple[UnitKey, UnitKey]]:
    """Pairs that may be the same document and have not been confirmed (at least one has
    samples)."""
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
                    continue  # Same file, or identical bytes (already grouped automatically).
                if a.unit not in members and b.unit not in members:
                    continue  # Neither has samples yet: decide later.
                if members.get(a.unit) is not None and members.get(a.unit) == members.get(b.unit):
                    continue
                if frozenset({a.unit, b.unit}) in decided:
                    continue
                if same_document(a, b):
                    found.append((a, b) if a.unit in members else (b, a))
    return found
