# ruff: noqa: E501
"""Replay: re-extract reviewed documents from their stored OCR snapshots with different settings and
compare against the reviewed values, to see which values the new settings fix and which they
break.

- Both arms (A and B) use the same OCR snapshot, so differences come only from extraction (model,
  prompt, examples) and not from OCR changes;
- A defaults to the stored results (not re-run) and B is the configuration under test; both arms
  may also be re-run;
- Pairing is grouped by business document; the documents of one group form a single bootstrap
  unit;
- Read-only: nothing in the database is changed (review data, samples and splits are untouched);
  results are written to the --out directory.

Scope: single fields and table line items. Line items first pair reviewed rows with extracted
rows (maximum matching as in DocILE line item recognition; see eval/tables.py) and are then
compared cell by cell; single fields and line-item cells are reported separately."""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import comb
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.i18n import render
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateVersion
from jacara.pipeline.current import template_from_version
from jacara.pipeline.learn import Lesson, from_database
from jacara.pipeline.process import choose_template, suffix_for
from jacara.pipeline.run import Pipeline, TemplateChoice
from jacara.pipeline.snapshots import load_snapshot
from jacara.pipeline.vision import VisionMode
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors.base import split_cell_name
from jacara.plugins.ocr import OcrPage
from jacara.plugins.ocr.loader import PdfRead

if TYPE_CHECKING:
    from jacara.eval.tables import CellResult, PredRow, TableSide, TruthRow

Outcome = Literal["correct", "wrong", "missing", "extra", "empty"]
"""Outcome of one field: correct, wrong value, missed, spurious, or both empty (not counted)."""

BOOTSTRAP_ROUNDS = 2000


@dataclass(frozen=True)
class Arm:
    """Configuration of one arm. server=None means the stored results, which are not re-run."""

    name: str
    label: str
    server: str | None = None
    learn: str | None = None
    """Include past corrections as examples during extraction: recent or curated; None for none.
    Documents in the locked test split are never used as examples."""

    @property
    def stored(self) -> bool:
        return self.server is None


@dataclass(frozen=True)
class Pred:
    value: str | None
    flagged: bool
    """Flagged for review."""


@dataclass
class Row:
    """Outcome of one field in both arms."""

    document_id: str
    page: int
    group: str
    split: str | None
    template: str
    field: str
    type: str
    truth: str | None
    a: Pred
    b: Pred

    @property
    def a_outcome(self) -> Outcome:
        return outcome(self.type, self.truth, self.a.value)

    @property
    def b_outcome(self) -> Outcome:
        return outcome(self.type, self.truth, self.b.value)


@dataclass
class CellPair:
    """Outcome of one reviewed line-item cell in both arms (each arm is paired separately, so the
    matched rows may differ)."""

    document_id: str
    page: int
    group: str
    split: str | None
    template: str
    a: CellResult
    b: CellResult

    @property
    def a_outcome(self) -> Outcome:
        return self.a.outcome

    @property
    def b_outcome(self) -> Outcome:
        return self.b.outcome


@dataclass
class TableExtra:
    """Extracted cells in one arm with no corresponding reviewed cell on a document: number of
    spurious cells and rows."""

    cells: int = 0
    rows: int = 0


class _Paired(Protocol):
    """Unit for grouped bootstrap: a single field (Row) or a line-item cell (CellPair)."""

    @property
    def group(self) -> str: ...
    @property
    def a_outcome(self) -> Outcome: ...
    @property
    def b_outcome(self) -> Outcome: ...


@dataclass
class Skipped:
    document_id: str
    reason: str


@dataclass
class Manifest:
    """What this replay used, so results can be interpreted later."""

    started_at: str
    jacara_version: str
    arms: list[dict[str, Any]]
    split_version: dict[str, int] = field(default_factory=dict)
    templates: dict[str, int] = field(default_factory=dict)
    ocr: dict[str, int] = field(default_factory=dict)
    snapshots: dict[str, str] = field(default_factory=dict)
    """Document id → sha256 of the OCR snapshot: proves both arms used the same text."""
    reread: list[str] = field(default_factory=list)
    """Documents without a snapshot that were re-OCR'd (their differences include OCR changes)."""


@dataclass
class ReplayResult:
    rows: list[Row]
    skipped: list[Skipped]
    manifest: Manifest
    models: dict[str, Counter[str]] = field(default_factory=dict)
    """Model and prompt version actually used by each arm ("model / prompt" → count)."""
    cells: list[CellPair] = field(default_factory=list)
    """Line items: every reviewed cell."""
    extra: dict[str, TableExtra] = field(default_factory=dict)
    """Line items: spurious cells and rows per arm (A, B → totals)."""


# ---- Comparison ----


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None


def same_value(type_: str, a: str | None, b: str | None) -> bool:
    """Whether two values are equal: numbers compare numerically (9 equals 9.0); other values after
    collapsing whitespace."""
    if a is None or b is None:
        return (a is None or not a.strip()) and (b is None or not b.strip())
    if type_ == "number":
        x, y = _decimal(a), _decimal(b)
        if x is not None and y is not None:
            return x == y
    return " ".join(a.split()) == " ".join(b.split())


def _empty(value: str | None) -> bool:
    return value is None or not value.strip()


def outcome(type_: str, truth: str | None, pred: str | None) -> Outcome:
    if _empty(truth) and _empty(pred):
        return "empty"
    if _empty(pred):
        return "missing"
    if _empty(truth):
        return "extra"
    return "correct" if same_value(type_, truth, pred) else "wrong"


def mcnemar_exact(fixed: int, broken: int) -> float:
    """Exact two-sided McNemar test: the probability of an imbalance at least this large if fixes
    and breaks were equally likely."""
    n = fixed + broken
    if n == 0:
        return 1.0
    k = min(fixed, broken)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def _accuracy(rows: Sequence[_Paired], arm: Literal["a", "b"]) -> float:
    counted = [r for r in rows if (r.a_outcome if arm == "a" else r.b_outcome) != "empty"]
    if not counted:
        return 0.0
    right = sum((r.a_outcome if arm == "a" else r.b_outcome) == "correct" for r in counted)
    return right / len(counted)


def group_bootstrap(
    rows: Sequence[_Paired], rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0
) -> tuple[float, float] | None:
    """95% interval of the accuracy difference B − A, resampling business-document groups with
    replacement. Not computed for fewer than 2 groups."""
    groups: dict[str, list[_Paired]] = {}
    for r in rows:
        groups.setdefault(r.group, []).append(r)
    if len(groups) < 2:
        return None
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311  Statistical sampling, not cryptography.
    diffs: list[float] = []
    for _ in range(rounds):
        picked = [r for _ in keys for r in groups[rng.choice(keys)]]
        diffs.append(_accuracy(picked, "b") - _accuracy(picked, "a"))
    diffs.sort()
    return diffs[int(0.025 * rounds)], diffs[min(rounds - 1, int(0.975 * rounds))]


def evidence_level(groups: int, rows: int) -> str:
    """Same thresholds as the OCR sample export."""
    if groups < 10 or rows < 50:
        return "process"
    if groups >= 15 and rows >= 100:
        return "trend"
    return "thin"


EVIDENCE_TEXT = {
    "process": "只能说明流程跑通了：业务单据少于 10 组或字段少于 50 个，差别不能当结论",
    "thin": "证据偏少：可以看方向，别据此换设置",
    "trend": "可以看趋势（仍然只是这批单据上的结果）",
}


def summarize(result: ReplayResult) -> dict[str, Any]:
    rows = result.rows
    arms = {a["name"]: a for a in result.manifest.arms}

    def side(name: Literal["a", "b"]) -> dict[str, Any]:
        outcomes = Counter(r.a_outcome if name == "a" else r.b_outcome for r in rows)
        preds = [r.a if name == "a" else r.b for r in rows]
        counted = len(rows) - outcomes["empty"]
        silent = sum(
            (r.a_outcome if name == "a" else r.b_outcome) in ("wrong", "missing", "extra")
            and not (r.a if name == "a" else r.b).flagged
            for r in rows
        )
        return {
            "label": arms[name.upper()]["label"],
            "fields": counted,
            "correct": outcomes["correct"],
            "wrong": outcomes["wrong"],
            "missing": outcomes["missing"],
            "extra": outcomes["extra"],
            "accuracy": round(_accuracy(rows, name), 4),
            "flagged": sum(p.flagged for p in preds),
            "flagged_rate": round(sum(p.flagged for p in preds) / len(rows), 4) if rows else 0.0,
            "silent_errors": silent,
        }

    fixed = [
        r for r in rows if r.a_outcome in ("wrong", "missing", "extra") and r.b_outcome == "correct"
    ]
    broken = [
        r for r in rows if r.a_outcome == "correct" and r.b_outcome in ("wrong", "missing", "extra")
    ]
    new_missing = [r for r in rows if r.b_outcome == "missing" and r.a_outcome != "missing"]
    groups = {r.group for r in rows}
    ci = group_bootstrap(rows)
    level = evidence_level(len(groups), len(rows))
    by_field: dict[str, dict[str, int]] = {}
    for r in rows:
        stat = by_field.setdefault(
            f"{r.template}:{r.field}", {"fields": 0, "fixed": 0, "broken": 0}
        )
        stat["fields"] += 1
        stat["fixed"] += r in fixed
        stat["broken"] += r in broken
    return {
        "documents": len({r.document_id for r in rows}),
        "groups": len(groups),
        "rows": len(rows),
        "splits": dict(Counter(r.split or "未分组" for r in rows)),
        "a": side("a"),
        "b": side("b"),
        "fixed": len(fixed),
        "broken": len(broken),
        "new_missing": len(new_missing),
        "flag_on": sum(r.b.flagged and not r.a.flagged for r in rows),
        "flag_off": sum(r.a.flagged and not r.b.flagged for r in rows),
        "mcnemar_p": round(mcnemar_exact(len(fixed), len(broken)), 4),
        "bootstrap_ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "evidence": level,
        "evidence_text": EVIDENCE_TEXT[level],
        "by_field": dict(
            sorted(by_field.items(), key=lambda kv: -(kv[1]["fixed"] + kv[1]["broken"]))
        ),
        "tables": _table_summary(result),
        "skipped": [asdict(s) for s in result.skipped],
        "models": {k: dict(v) for k, v in result.models.items()},
        "manifest": asdict(result.manifest),
    }


def _table_summary(result: ReplayResult) -> dict[str, Any] | None:
    """Line items: micro precision, recall and F1 per arm (the primary DocILE LIR metric), and
    per-cell changes between arms (exact McNemar and grouped bootstrap, as for single fields)."""
    from jacara.eval.tables import TableSide, micro

    cells = result.cells
    if not cells:
        return None
    arms = {a["name"]: a for a in result.manifest.arms}

    def side(name: Literal["a", "b"]) -> dict[str, Any]:
        extra = result.extra.get(name.upper(), TableExtra())
        m = micro([TableSide([c.a if name == "a" else c.b for c in cells], extra.cells, 0)])
        return {
            "label": arms[name.upper()]["label"],
            "correct": m.correct,
            "wrong": m.wrong,
            "missing": m.missing,
            "extra": m.extra,
            "extra_rows": extra.rows,
            "precision": round(m.precision, 4),
            "recall": round(m.recall, 4),
            "f1": round(m.f1, 4),
            "flagged": sum((c.a if name == "a" else c.b).pred.flagged for c in cells),
            "silent_errors": sum(
                (c.a_outcome if name == "a" else c.b_outcome) in ("wrong", "missing", "extra")
                and not (c.a if name == "a" else c.b).pred.flagged
                for c in cells
            ),
        }

    bad = ("wrong", "missing", "extra")
    fixed = [c for c in cells if c.a_outcome in bad and c.b_outcome == "correct"]
    broken = [c for c in cells if c.a_outcome == "correct" and c.b_outcome in bad]
    groups = {c.group for c in cells}
    counted = [c for c in cells if c.a_outcome != "empty" or c.b_outcome != "empty"]
    ci = group_bootstrap(counted)
    level = evidence_level(len(groups), len(counted))
    by_column: dict[str, dict[str, int]] = {}
    for c in cells:
        stat = by_column.setdefault(
            f"{c.template}:{c.a.table}.{c.a.column}", {"cells": 0, "fixed": 0, "broken": 0}
        )
        stat["cells"] += 1
        stat["fixed"] += c in fixed
        stat["broken"] += c in broken
    return {
        "documents": len({c.document_id for c in cells}),
        "groups": len(groups),
        "rows": len({(c.document_id, c.page, c.a.table, c.a.row) for c in cells}),
        "cells": len(counted),
        "a": side("a"),
        "b": side("b"),
        "fixed": len(fixed),
        "broken": len(broken),
        "mcnemar_p": round(mcnemar_exact(len(fixed), len(broken)), 4),
        "bootstrap_ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "evidence": level,
        "evidence_text": EVIDENCE_TEXT[level],
        "by_column": dict(
            sorted(by_column.items(), key=lambda kv: -(kv[1]["fixed"] + kv[1]["broken"]))
        ),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def summary_markdown(s: dict[str, Any]) -> str:
    a, b = s["a"], s["b"]
    ci = s["bootstrap_ci"]
    lines = [
        "# 回放结果",
        "",
        f"- 比较：**A {a['label']}** 对 **B {b['label']}**，同一份 OCR 快照，和人审核过的值比",
        f"- {s['documents']} 份文档、{s['groups']} 组业务单据、{s['rows']} 个字段（单个字段；表格明细见下面单独一节）",
        f"- 证据：{s['evidence_text']}",
        "",
        "| | A | B |",
        "|---|---|---|",
        f"| 准确率 | {_pct(a['accuracy'])} | {_pct(b['accuracy'])} |",
        f"| 对 / 错 / 漏 / 多 | {a['correct']} / {a['wrong']} / {a['missing']} / {a['extra']} | {b['correct']} / {b['wrong']} / {b['missing']} / {b['extra']} |",
        f"| 标黄率 | {_pct(a['flagged_rate'])} | {_pct(b['flagged_rate'])} |",
        f"| 错了没标黄 | {a['silent_errors']} | {b['silent_errors']} |",
        "",
        f"- B 修好了 **{s['fixed']}** 个、弄坏了 **{s['broken']}** 个（精确 McNemar p = {s['mcnemar_p']}）",
        f"- B 新漏掉的：{s['new_missing']} 个",
        f"- 标黄变化：B 新标黄 {s['flag_on']} 个、不再标黄 {s['flag_off']} 个",
        (
            f"- 准确率差（B − A）按业务单据分组 bootstrap 的 95% 区间：{_pct(ci[0])} ～ {_pct(ci[1])}"
            if ci
            else "- 业务单据不到 2 组，不做 bootstrap"
        ),
        "",
    ]
    changed = [(k, v) for k, v in s["by_field"].items() if v["fixed"] or v["broken"]]
    if changed:
        lines += [
            "## 哪些字段变了",
            "",
            "| 模板:字段 | 字段数 | 修好 | 弄坏 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {k} | {v['fields']} | {v['fixed']} | {v['broken']} |" for k, v in changed[:30]
        ]
        lines.append("")
    if s["tables"]:
        lines += _tables_markdown(s["tables"])
    if s["skipped"]:
        reasons = Counter(x["reason"] for x in s["skipped"])
        lines += ["## 跳过的文档", ""] + [f"- {r}：{n} 份" for r, n in reasons.most_common()] + [""]
    if s["manifest"]["reread"]:
        lines += [
            f"注意：{len(s['manifest']['reread'])} 份没有 OCR 快照，重新认了字（--reread），"
            "这些的差别里混着 OCR 的变化。",
            "",
        ]
    lines += [
        "## 用的是什么",
        "",
        *[
            f"- {side}：{', '.join(f'{k}（{n} 张）' for k, n in counts.items())}"
            for side, counts in s["models"].items()
        ],
        f"- 模板：{', '.join(f'{k} 第 {v} 版' for k, v in s['manifest']['templates'].items()) or '无'}",
        f"- OCR：{', '.join(s['manifest']['ocr']) or '无'}",
        f"- jacara {s['manifest']['jacara_version']}，{s['manifest']['started_at']}",
        "",
        "fields.jsonl、cells.jsonl 里有每个字段和明细格子的原值，是你的业务数据：只放在本机，别提交、别外传。",
    ]
    return "\n".join(lines) + "\n"


def _tables_markdown(t: dict[str, Any]) -> list[str]:
    a, b = t["a"], t["b"]
    ci = t["bootstrap_ci"]
    lines = [
        "## 表格明细",
        "",
        f"- {t['documents']} 份文档、{t['groups']} 组业务单据、人审核后 {t['rows']} 行、{t['cells']} 个格子",
        "- 先把人审核后的行和抽出来的行一一配对（对上的格子总数最多的配法，DocILE 明细识别评测），再逐格比；"
        "一格也没对上的不算配上",
        f"- 证据：{t['evidence_text']}",
        "",
        "| | A | B |",
        "|---|---|---|",
        f"| 精确率 / 召回率 / F1（micro） | {_pct(a['precision'])} / {_pct(a['recall'])} / {_pct(a['f1'])} | {_pct(b['precision'])} / {_pct(b['recall'])} / {_pct(b['f1'])} |",
        f"| 对 / 错 / 漏 / 多 | {a['correct']} / {a['wrong']} / {a['missing']} / {a['extra']} | {b['correct']} / {b['wrong']} / {b['missing']} / {b['extra']} |",
        f"| 多出来的行 | {a['extra_rows']} | {b['extra_rows']} |",
        f"| 错了没标黄 | {a['silent_errors']} | {b['silent_errors']} |",
        "",
        f"- B 修好了 **{t['fixed']}** 格、弄坏了 **{t['broken']}** 格（精确 McNemar p = {t['mcnemar_p']}）",
        (
            f"- 准确率差（B − A）按业务单据分组 bootstrap 的 95% 区间：{_pct(ci[0])} ～ {_pct(ci[1])}"
            if ci
            else "- 业务单据不到 2 组，不做 bootstrap"
        ),
        "",
    ]
    changed = [(k, v) for k, v in t["by_column"].items() if v["fixed"] or v["broken"]]
    if changed:
        lines += ["| 模板:表.列 | 格子数 | 修好 | 弄坏 |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['cells']} | {v['fixed']} | {v['broken']} |" for k, v in changed[:30]]
        lines.append("")
    return lines


def write_result(result: ReplayResult, out: Path) -> Path:
    """Write summary.md, summary.json, fields.jsonl and cells.jsonl (when line items exist); return
    the path of summary.md."""
    out.mkdir(parents=True, exist_ok=True)
    s = summarize(result)
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "fields.jsonl").open("w", encoding="utf-8") as f:
        for r in result.rows:
            item = asdict(r) | {"a_outcome": r.a_outcome, "b_outcome": r.b_outcome}
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    if result.cells:
        with (out / "cells.jsonl").open("w", encoding="utf-8") as f:
            for c in result.cells:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    md = out / "summary.md"
    md.write_text(summary_markdown(s), encoding="utf-8")
    return md


# ---- Replay ----


@dataclass
class Options:
    template: str | None = None
    docs: list[uuid.UUID] | None = None
    limit: int | None = None
    split: str | None = None
    """Replay only documents in this split: train, val, test_locked; all when omitted."""
    reread: bool = False
    """Re-OCR documents without an OCR snapshot (processed before snapshots existed); skipped when
    omitted."""


@dataclass
class _Unit:
    page: int
    group: str
    split: str | None
    template: str
    values: dict[str, tuple[str, str | None, Pred]]
    """Field name → (type, reviewed value, stored result)."""
    tables: dict[str, list[TruthRow]] = field(default_factory=dict)
    """Table name → reviewed rows (in display order; deleted rows excluded, rows added by reviewers
    included)."""
    stored_tables: dict[str, list[PredRow]] = field(default_factory=dict)
    """Table name → rows extracted by the model at the time (in model order, including rows later
    deleted by reviewers)."""


@dataclass
class _Doc:
    id: uuid.UUID
    team_id: uuid.UUID
    filename: str
    content_type: str | None
    storage_key: str | None
    split: str
    vision: VisionMode | None
    pdf_read: PdfRead | None
    snapshot: str | None
    template: TemplateChoice
    units: dict[int, _Unit]


def _stored(extraction: Extraction) -> dict[str, tuple[str, str | None, Pred]]:
    found: dict[str, tuple[str, str | None, Pred]] = {}
    for v in extraction.values:
        if v.origin not in ("model", "manual") or split_cell_name(v.name) is not None:
            continue  # Formula fields are excluded; line items are handled separately.
        truth = None if v.deleted else v.current_value
        found[v.name] = (v.type, truth, Pred(v.value, bool(v.needs_review)))
    return found


def _stored_tables(
    extraction: Extraction,
) -> tuple[dict[str, list[TruthRow]], dict[str, list[PredRow]]]:
    """Ground truth and stored results for line items.

        The ground truth is the current reviewed result (pipeline/current.py: deleted rows
    skipped, numbered in display order); formula columns are excluded (as for single fields).
    Stored results contain only model-extracted cells (the model never produced rows added by
    reviewers)."""
    from jacara.pipeline.current import current_values

    truth: dict[str, dict[int, dict[str, tuple[str, str | None]]]] = {}
    for value, shown in current_values(extraction):
        cell = split_cell_name(shown)
        if cell is None or value.origin not in ("model", "manual"):
            continue
        table, index, column = cell
        truth.setdefault(table, {}).setdefault(index, {})[column] = (
            value.type,
            value.current_value,
        )
    stored: dict[str, dict[int, dict[str, Pred]]] = {}
    for value in extraction.values:
        cell = split_cell_name(value.name)
        if cell is None or value.origin != "model":
            continue
        table, index, column = cell
        stored.setdefault(table, {}).setdefault(index, {})[column] = Pred(
            value.value, bool(value.needs_review)
        )
    return (
        {t: [rows[i] for i in sorted(rows)] for t, rows in truth.items()},
        {t: [rows[i] for i in sorted(rows)] for t, rows in stored.items()},
    )


def _template_of(session: Session, document: Document) -> TemplateChoice | None:
    """Use the same template version as originally; automatic selection uses the currently
    selectable templates; key-value discovery is not compared (it has no fixed fields)."""
    if document.discover:
        return None
    versions = {e.template_version_id for e in document.extractions}
    if len(versions) == 1 and None not in versions and not document.auto:
        version = session.get(TemplateVersion, next(iter(versions)))
        if version is not None:
            return template_from_version(version)
    return choose_template(session, document)[0]


def _collect(
    session: Session, options: Options, manifest: Manifest
) -> tuple[list[_Doc], list[Skipped]]:
    from jacara.modules.ocr_samples.groups import groups_of, splits_of

    query = select(Document).where(Document.status == "reviewed").order_by(Document.reviewed_at)
    if options.docs:
        query = query.where(Document.id.in_(options.docs))
    documents = session.scalars(query).all()
    docs: list[_Doc] = []
    skipped: list[Skipped] = []
    team_groups: dict[uuid.UUID, tuple[dict[Any, uuid.UUID], dict[uuid.UUID, Any]]] = {}
    for document in documents:
        if options.limit is not None and len(docs) >= options.limit:
            break
        extractions = list(document.extractions)
        if options.template and not any(e.template == options.template for e in extractions):
            continue
        template = _template_of(session, document)
        if template is None:
            skipped.append(Skipped(str(document.id), "键值对发现：没有固定字段"))
            continue
        snapshots = {e.ocr_snapshot for e in extractions if e.ocr_snapshot}
        snapshot = next(iter(snapshots)) if len(snapshots) == 1 else None
        if snapshot is None and not options.reread:
            skipped.append(
                Skipped(str(document.id), "没有 OCR 快照（这次更新前处理的；加 --reread 重新认字）")
            )
            continue
        if document.storage_key is None:
            skipped.append(Skipped(str(document.id), "没有原始文件"))
            continue
        if document.team_id not in team_groups:
            team_groups[document.team_id] = (
                groups_of(session, document.team_id),
                splits_of(session, document.team_id),
            )
        groups, splits = team_groups[document.team_id]
        units: dict[int, _Unit] = {}
        for e in extractions:
            group = groups.get((document.id, e.page))
            assigned = splits.get(group) if group is not None else None
            split = assigned.split if assigned is not None else None
            if options.split and split != options.split:
                continue
            truth_tables, stored_tables = _stored_tables(e)
            units[e.page] = _Unit(
                e.page,
                str(group) if group is not None else f"doc:{document.id}:{e.page}",
                split,
                e.template,
                _stored(e),
                truth_tables,
                stored_tables,
            )
            manifest.templates[e.template] = e.template_version
        if not units:
            continue
        docs.append(
            _Doc(
                document.id,
                document.team_id,
                document.filename,
                document.content_type,
                document.storage_key,
                document.split,
                cast(VisionMode | None, document.vision),
                cast(PdfRead | None, document.pdf_read),
                snapshot,
                template,
                units,
            )
        )
    return docs, skipped


def _predictions(reports: Iterable[DocumentReport]) -> dict[int, dict[str, Pred]]:
    """Prediction for every field on every page (including table cells, named like
    items.2.amount)."""
    found: dict[int, dict[str, Pred]] = {}
    for report in reports:
        flagged = report.checks.fields_with_issues()
        found[report.page] = {
            a.field.name: Pred(
                None if a.field.value is None or a.status == "rejected" else str(a.field.value),
                a.needs_review or a.field.name in flagged,
            )
            for a in report.aligned
        }
    return found


def _pred_tables(preds: dict[str, Pred]) -> dict[str, list[PredRow]]:
    """Group cells such as items.2.amount into rows by table and row number."""
    tables: dict[str, dict[int, dict[str, Pred]]] = {}
    for name, pred in preds.items():
        cell = split_cell_name(name)
        if cell is not None:
            table, index, column = cell
            tables.setdefault(table, {}).setdefault(index, {})[column] = pred
    return {t: [rows[i] for i in sorted(rows)] for t, rows in tables.items()}


def _compare_tables(
    truth: dict[str, list[TruthRow]], pred: dict[str, list[PredRow]]
) -> dict[str, TableSide]:
    from jacara.eval.tables import compare_table

    return {t: compare_table(t, truth.get(t, []), pred.get(t, [])) for t in sorted({*truth, *pred})}


def replay(
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    arms: tuple[Arm, Arm],
    pipelines: Callable[[Arm], Pipeline],
    options: Options | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> ReplayResult:
    """Replay: reads the database and never writes back. pipelines(arm) returns the pipeline of that
    arm (never called for stored arms)."""
    from jacara import __version__

    options = options or Options()
    manifest = Manifest(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        jacara_version=__version__,
        arms=[asdict(a) for a in arms],
    )
    session = factory()
    try:
        docs, skipped = _collect(session, options, manifest)
        from jacara.modules.ocr_samples.groups import split_version

        for team in {d.team_id for d in docs}:
            manifest.split_version[str(team)] = split_version(session, team)
    finally:
        # Read-only: the group seed generated on first use is not persisted here.
        session.rollback()
        session.close()

    built = {a.name: pipelines(a) for a in arms if not a.stored}
    models: dict[str, Counter[str]] = {a.name: Counter() for a in arms}
    rows: list[Row] = []
    cells: list[CellPair] = []
    extra = {a.name: TableExtra() for a in arms}
    for n, doc in enumerate(docs, start=1):
        progress(f"{n}/{len(docs)} {doc.filename}")
        try:
            data = storage.get(doc.storage_key or "")
        except StorageError:
            skipped.append(Skipped(str(doc.id), "对象存储里取不到原始文件"))
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"original{suffix_for(doc.filename, doc.content_type)}"
            path.write_bytes(data)
            pages = _pages(doc, storage, path, built, manifest)
            if pages is None:
                skipped.append(Skipped(str(doc.id), "OCR 快照读不出来"))
                continue
            preds: dict[str, dict[int, dict[str, Pred]]] = {}
            failed = None
            for arm in arms:
                if arm.stored:
                    preds[arm.name] = {
                        p: {k: v[2] for k, v in u.values.items()} for p, u in doc.units.items()
                    }
                    models[arm.name]["当时存下的结果"] += len(doc.units)
                    continue
                pipeline = built[arm.name]
                pipeline.lessons = _lessons(factory, doc) if arm.learn else None
                pipeline.learn_mode = arm.learn or "recent"
                try:
                    reports = pipeline.run(
                        path,
                        name=doc.filename,
                        template=doc.template,
                        split=doc.split,
                        vision=doc.vision,
                        pdf_read=doc.pdf_read,
                        pages=pages,
                    )
                except Exception as err:  # A failure in one document does not affect the others.
                    failed = f"{arm.name} 边处理出错：{type(err).__name__}: {(render(str(err)) or '')[:120]}"
                    break
                finally:
                    pipeline.lessons = None
                preds[arm.name] = _predictions(reports)
                for r in reports:
                    models[arm.name][f"{r.extraction.model} / {r.extraction.prompt_version}"] += 1
        if failed:
            skipped.append(Skipped(str(doc.id), failed))
            continue
        a_name, b_name = arms[0].name, arms[1].name
        for page, unit in doc.units.items():
            a, b = preds[a_name].get(page), preds[b_name].get(page)
            if a is None or b is None:
                skipped.append(Skipped(f"{doc.id}#{page}", "重新分单据和当时不同，这一张不比"))
                continue
            for name, (type_, truth, _) in unit.values.items():
                rows.append(
                    Row(
                        str(doc.id),
                        page,
                        unit.group,
                        unit.split,
                        unit.template,
                        name,
                        type_,
                        truth,
                        a.get(name, Pred(None, False)),
                        b.get(name, Pred(None, False)),
                    )
                )
            sides = {
                arm.name: _compare_tables(
                    unit.tables,
                    unit.stored_tables if arm.stored else _pred_tables(preds[arm.name][page]),
                )
                for arm in arms
            }
            for table, a_side in sides[a_name].items():
                b_side = sides[b_name][table]
                # cells in both arms are ordered by reviewed row and column, so they correspond
                # one-to-one.
                for a_cell, b_cell in zip(a_side.cells, b_side.cells, strict=True):
                    cells.append(
                        CellPair(
                            str(doc.id), page, unit.group, unit.split, unit.template, a_cell, b_cell
                        )
                    )
                for name, side in ((a_name, a_side), (b_name, b_side)):
                    extra[name].cells += side.extra_cells
                    extra[name].rows += side.extra_rows
    return ReplayResult(rows, skipped, manifest, models, cells, extra)


def _pages(
    doc: _Doc,
    storage: ObjectStorage,
    path: Path,
    built: dict[str, Pipeline],
    manifest: Manifest,
) -> list[OcrPage] | None:
    if doc.snapshot is not None:
        try:
            raw = storage.get(doc.snapshot)
            pages = load_snapshot(storage, doc.snapshot)
        except (StorageError, ValueError, KeyError):
            return None
        manifest.snapshots[str(doc.id)] = hashlib.sha256(raw).hexdigest()
    else:
        pipeline = next(iter(built.values()), None)
        if pipeline is None:
            return None
        pages = pipeline.read(path, doc.pdf_read)
        manifest.reread.append(str(doc.id))
    for p in pages:
        engine = p.engine + (
            f"（{', '.join(f'{k}={render(v)}' for k, v in sorted(p.models.items()))}）"
            if p.models
            else ""
        )
        manifest.ocr[engine] = manifest.ocr.get(engine, 0) + 1
    return pages


def _lessons(factory: sessionmaker[Session], doc: _Doc) -> Callable[[str], list[Lesson]]:
    def lessons(template: str) -> list[Lesson]:
        session = factory()
        try:
            return from_database(session, doc.team_id, template, exclude=doc.id)
        finally:
            session.rollback()
            session.close()

    return lessons
