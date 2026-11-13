# ruff: noqa: E501
"""回放（ADR 0053，B4 最小回放）：审核完成的单据，用当时存下的 OCR 快照，换一种设置再抽一次，
和人审核过的值比，看新设置修好了哪些、弄坏了哪些。

- 两边（A、B）用同一份 OCR 快照：差别只来自抽取（模型、提示词、例子），不混进 OCR 的变化；
- A 默认是"当时存下的结果"（不用再跑），B 是要试的新设置；也可以两边都重新跑；
- 按业务单据分组配对（ADR 0052 的单据分组），同一组的几张算一个单位做 bootstrap；
- 只读：不改数据库里的任何东西（审核数据、样本、划分都不动），结果写到 --out 的文件夹里。

比较的范围（最小版）：单个字段（不含表格明细）。表格逐行对齐以后再做。
"""

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
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

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

Outcome = Literal["correct", "wrong", "missing", "extra", "empty"]
"""一个字段的结果：对了、值错了、该有没取到、不该有却给了值、两边都空（不算）。"""

BOOTSTRAP_ROUNDS = 2000


@dataclass(frozen=True)
class Arm:
    """一边的设置。server 是 None 表示"当时存下的结果"，不重新跑。"""

    name: str
    label: str
    server: str | None = None
    learn: bool = False
    """抽取时带上以前人改过的例子（ADR 0045）。锁定测试集的单据本来就不当例子。"""

    @property
    def stored(self) -> bool:
        return self.server is None


@dataclass(frozen=True)
class Pred:
    value: str | None
    flagged: bool
    """标黄了（要人看）。"""


@dataclass
class Row:
    """一个字段在两边的结果。"""

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
class Skipped:
    document_id: str
    reason: str


@dataclass
class Manifest:
    """这次回放用的是什么：以后看结果时知道是怎么比出来的。"""

    started_at: str
    jacara_version: str
    arms: list[dict[str, Any]]
    split_version: dict[str, int] = field(default_factory=dict)
    templates: dict[str, int] = field(default_factory=dict)
    ocr: dict[str, int] = field(default_factory=dict)
    snapshots: dict[str, str] = field(default_factory=dict)
    """文档 id → OCR 快照的 sha256：两边用的确实是同一份字。"""
    reread: list[str] = field(default_factory=list)
    """没有快照、重新认了字的文档（这些的差别里混着 OCR 的变化）。"""


@dataclass
class ReplayResult:
    rows: list[Row]
    skipped: list[Skipped]
    manifest: Manifest
    models: dict[str, Counter[str]] = field(default_factory=dict)
    """每一边实际用的模型和提示词版本（"模型 / 提示词" → 张数）。"""


# ---- 比较 ----


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        return None


def same_value(type_: str, a: str | None, b: str | None) -> bool:
    """两个值算不算一样：数字按数值比（9 和 9.0 一样），别的去掉多余空白再比。"""
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
    """精确 McNemar（双侧）：修好的和弄坏的一样多的话，看到这么不平均的概率。"""
    n = fixed + broken
    if n == 0:
        return 1.0
    k = min(fixed, broken)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def _accuracy(rows: Sequence[Row], arm: Literal["a", "b"]) -> float:
    counted = [r for r in rows if (r.a_outcome if arm == "a" else r.b_outcome) != "empty"]
    if not counted:
        return 0.0
    right = sum((r.a_outcome if arm == "a" else r.b_outcome) == "correct" for r in counted)
    return right / len(counted)


def group_bootstrap(
    rows: Sequence[Row], rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0
) -> tuple[float, float] | None:
    """B 减 A 的准确率差，按业务单据分组有放回地抽，取 95% 区间。少于 2 组不算。"""
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(r.group, []).append(r)
    if len(groups) < 2:
        return None
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311  统计抽样用，不是加密
    diffs: list[float] = []
    for _ in range(rounds):
        picked = [r for _ in keys for r in groups[rng.choice(keys)]]
        diffs.append(_accuracy(picked, "b") - _accuracy(picked, "a"))
    diffs.sort()
    return diffs[int(0.025 * rounds)], diffs[min(rounds - 1, int(0.975 * rounds))]


def evidence_level(groups: int, rows: int) -> str:
    """和 OCR 样本导出（ADR 0052）一样的门槛。"""
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
        "skipped": [asdict(s) for s in result.skipped],
        "models": {k: dict(v) for k, v in result.models.items()},
        "manifest": asdict(result.manifest),
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def summary_markdown(s: dict[str, Any]) -> str:
    a, b = s["a"], s["b"]
    ci = s["bootstrap_ci"]
    lines = [
        "# 回放结果（ADR 0053）",
        "",
        f"- 比较：**A {a['label']}** 对 **B {b['label']}**，同一份 OCR 快照，和人审核过的值比",
        f"- {s['documents']} 份文档、{s['groups']} 组业务单据、{s['rows']} 个字段（单个字段，不含表格明细）",
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
        "fields.jsonl 里有每个字段的原值，是你的业务数据：只放在本机，别提交、别外传。",
    ]
    return "\n".join(lines) + "\n"


def write_result(result: ReplayResult, out: Path) -> Path:
    """写出 summary.md、summary.json、fields.jsonl，返回 summary.md 的路径。"""
    out.mkdir(parents=True, exist_ok=True)
    s = summarize(result)
    (out / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "fields.jsonl").open("w", encoding="utf-8") as f:
        for r in result.rows:
            item = asdict(r) | {"a_outcome": r.a_outcome, "b_outcome": r.b_outcome}
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    md = out / "summary.md"
    md.write_text(summary_markdown(s), encoding="utf-8")
    return md


# ---- 回放 ----


@dataclass
class Options:
    template: str | None = None
    docs: list[uuid.UUID] | None = None
    limit: int | None = None
    split: str | None = None
    """只回放这一边的单据：train、val、test_locked；不写是全部。"""
    reread: bool = False
    """没有 OCR 快照的（这次更新前处理的）重新认字；不写就跳过它们。"""


@dataclass
class _Unit:
    page: int
    group: str
    split: str | None
    template: str
    values: dict[str, tuple[str, str | None, Pred]]
    """字段名 → (类型, 人审核过的值, 当时存下的结果)。"""


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
            continue  # 按公式算的不算；表格明细这一版不比
        truth = None if v.deleted else v.current_value
        found[v.name] = (v.type, truth, Pred(v.value, bool(v.needs_review)))
    return found


def _template_of(session: Session, document: Document) -> TemplateChoice | None:
    """和当时同一版模板；自动判断的按现在能选的；键值对发现不比（没有固定字段）。"""
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
            units[e.page] = _Unit(
                e.page,
                str(group) if group is not None else f"doc:{document.id}:{e.page}",
                split,
                e.template,
                _stored(e),
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


def replay(
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    arms: tuple[Arm, Arm],
    pipelines: Callable[[Arm], Pipeline],
    options: Options | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> ReplayResult:
    """回放：只读数据库，不写回任何东西。pipelines(arm) 给出那一边用的流水线（stored 的不会叫）。"""
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
        session.rollback()  # 只读：分组设置第一次用时会生成种子，这里不留下
        session.close()

    built = {a.name: pipelines(a) for a in arms if not a.stored}
    models: dict[str, Counter[str]] = {a.name: Counter() for a in arms}
    rows: list[Row] = []
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
                except Exception as err:  # 一份出错不影响别的
                    failed = f"{arm.name} 边处理出错：{type(err).__name__}: {str(err)[:120]}"
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
    return ReplayResult(rows, skipped, manifest, models)


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
            f"（{', '.join(f'{k}={v}' for k, v in sorted(p.models.items()))}）" if p.models else ""
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
