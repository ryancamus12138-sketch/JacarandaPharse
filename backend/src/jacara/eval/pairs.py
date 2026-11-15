"""Paired evaluation (development only): compare field accuracy of several reading strategies on a
photo and a PDF of the same document.

Manifest (TOML, under data/eval/; data/ is not tracked by Git, so real documents never enter the
repository):

    [[doc]]
    id = "lab-1"
    template = "lab_report"
    pdf = "../samples/lab-report.pdf"
    photo = "../samples/lab-report-photo.jpg"   # optional: defaults to a lossless PNG render of
the PDF (controlled comparison)
    [doc.truth]
    patient_name = "Jane Doe"
    collected_at = "2026-11-10"

Arms:
- A photo: the photo through the current pipeline (cross-checking and zoomed re-recognition
  enabled).
- B PDF, OCR only: the PDF rendered and OCR'd, without cross-checking against the text layer.
- C PDF + text-layer candidates: additionally cross-checked field by field against the text layer
  and form fields, without zooming.
- D PDF + zoomed re-recognition: C plus zoomed re-recognition (the current default).
- E legacy: the PDF text layer only (behavior before 2026-11; the baseline).

Two kinds of model:
- oracle (no model server): the ground truth is used as the model answer, measuring whether
  cross-checking drops correct values and how many false alerts it raises; a second pass removes
  all answers to measure whether missed values can be recovered from candidates. This measures
  OCR, the text layer and cross-checking themselves.
- llm (--server): real model extraction, measuring final field accuracy.

Metrics: field accuracy before review (missing counts as wrong), key-field accuracy, share of
flagged values, number of wrong values not flagged, per-page latency P50/P95, regressions A→D and
B→D, number of fixes, and a document-paired bootstrap interval of D−A."""

from __future__ import annotations

import io
import json
import random
import statistics
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from jacara.core.i18n import render
from jacara.eval.score import same
from jacara.pipeline.fieldcheck import is_key
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult
from jacara.templates import STARTERS, Template, load_starter, load_template

ARMS = {
    "A": "照片",
    "B": "PDF 只用 OCR",
    "C": "PDF + 文字层候选",
    "D": "PDF + 局部放大（默认）",
    "E": "旧做法：只读文字层",
}


@dataclass(frozen=True)
class PairDoc:
    id: str
    template: str
    pdf: Path | None
    photo: Path | None
    truth: dict[str, str]
    template_file: Path | None = None
    """My templates: definition files exported with the review data (templates/*.json)."""

    @property
    def kind(self) -> str:
        if self.pdf is None:
            return "只有图片"
        return "真实成对" if self.photo is not None else "受控对照"

    def load(self) -> Template:
        if self.template_file is not None:
            from jacara.templates import from_definition

            data = json.loads(self.template_file.read_text("utf-8"))
            return from_definition(
                data["definition"], data["name"], int(data.get("version", 1)), data.get("title", "")
            )
        if self.template in STARTERS:
            return load_starter(self.template)
        return load_template(self.template)


def load_manifest(path: Path) -> list[PairDoc]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    docs = []
    for d in data.get("doc", []):
        photo, pdf, file = d.get("photo"), d.get("pdf"), d.get("template_file")
        docs.append(
            PairDoc(
                id=str(d["id"]),
                template=str(d.get("template", "")),
                pdf=(base / pdf).resolve() if pdf else None,
                photo=(base / photo).resolve() if photo else None,
                truth={k: str(v) for k, v in d.get("truth", {}).items()},
                template_file=(base / file).resolve() if file else None,
            )
        )
    return docs


class OracleExtractor:
    """Use the ground truth as the model answer (no model server). With drop=True no value is given,
    simulating a model that misses everything."""

    name = "oracle"

    def __init__(self) -> None:
        self.truth: dict[str, str] = {}
        self.drop = False

    def extract(self, page, specs, table_specs=(), structure=None, images=()):
        fields = []
        for spec in specs:
            raw = None if self.drop else self.truth.get(spec.name)
            value: str | float | None = raw
            if raw is not None and spec.type == "number":
                try:
                    value = float(raw.replace(",", ""))
                except ValueError:
                    value = raw
            blocks = _blocks_of(structure, spec.type, value) if value is not None else ()
            fields.append(ExtractedField(spec.name, spec.type, value, blocks=blocks))
        names = {s.name for s in specs}
        for name, raw in self.truth.items():  # Table cell: items.2.amount
            if name not in names and "." in name and not self.drop:
                blocks = _blocks_of(structure, "string", raw)
                fields.append(ExtractedField(name, "string", raw, blocks=blocks))
        return ExtractionResult(tuple(fields), "", "oracle", "oracle", "oracle", 0)

    def discover(self, page, structure=None):
        return ExtractionResult((), "", "oracle", "oracle", "oracle", 0)


def _blocks_of(structure: Any, type_: str, value: str | float) -> tuple[int, ...]:
    """Name the text blocks containing the value, as the model would: blocks whose text contains it
    (used when the ground truth stands in for the model).

        When OCR misread the value, no block contains it and no index is given; cross-checking
    then handles it, as when the model answers with the misread text."""
    from jacara.core.cjk import plain_cjk
    from jacara.plugins.aligners.text_match import (
        _squash,
        _tidy,
    )

    if structure is None:
        return ()
    texts = [
        (n, plain_cjk(w.text))
        for n in range(1, len(structure.words) + 1)
        if (w := structure.word(n))
    ]
    if type_ == "number" and isinstance(value, float):
        from jacara.core.numbers import number_pattern

        found = number_pattern(value, loose=True)
        return next(((n,) for n, t in texts if found.search(_tidy(t))), ())
    if type_ == "date":
        from jacara.core.textvalue import parse_date

        # First find blocks from which this date can be parsed, then search by spelling
        # ("INV-20240510" also contains 20240510).
        whole_date = next(((n,) for n, t in texts if str(parse_date(t)) == str(value)), ())
        if whole_date:
            return whole_date
        from jacara.core.dates import date_pattern

        pattern = date_pattern(value)
        return next(((n,) for n, t in texts if pattern.search(_tidy(t))), ())
    wanted = _squash(plain_cjk(str(value)))
    whole = next(((n,) for n, t in texts if wanted and wanted in _squash(t)), ())
    if whole:
        return whole
    parts = [n for n, t in texts if len(_squash(t)) >= 2 and _squash(t) in wanted]
    return tuple(parts[:6])


@dataclass
class FieldOutcome:
    doc: str
    arm: str
    name: str
    key: bool
    right: bool
    flagged: bool
    recovered: bool | None = None
    """Pass without answers (oracle): whether the ground truth is among the candidates."""
    value: str | None = None
    """Value given by the model (after cross-checking), for error analysis."""
    gold: str = ""


@dataclass
class ArmResult:
    arm: str
    fields: list[FieldOutcome] = field(default_factory=list)
    seconds_per_page: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _value_of(report: DocumentReport, name: str) -> tuple[Any, bool, Any]:
    for a in report.aligned:
        if a.field.name == name:
            value = None if a.status == "rejected" else a.field.value
            return value, a.needs_review, a
    return None, False, None


def _type_of(template: Template, name: str) -> str:
    for f in template.fields:
        if f.name == name:
            return f.type
    return "string"


def _key(template: Template, name: str) -> bool:
    spec = next((f for f in template.fields if f.name == name), None)
    return is_key(_type_of(template, name), spec)


def _photo_of(doc: PairDoc, scratch: Path) -> Path:
    """Controlled comparison: the first PDF page rendered as a lossless PNG (the same pixels the PDF
    path sends to OCR)."""
    if doc.photo is not None:
        return doc.photo
    from jacara.plugins.ocr.loader import page_png

    assert doc.pdf is not None  # noqa: S101  The manifest requires at least one of pdf and photo.

    out = scratch / f"{doc.id}.png"
    out.write_bytes(page_png(doc.pdf.read_bytes(), True, 1))
    return out


def run_pairs(
    docs: Sequence[PairDoc],
    make_pipeline: Callable[[str], Any],
    oracle: OracleExtractor | None,
    scratch: Path,
    progress: Callable[[str], None] = print,
    arms: Sequence[str] = tuple(ARMS),
    learn: dict[str, list[tuple[str, Any]]] | None = None,
) -> dict[str, ArmResult]:
    """Run the arms (A–E by default) on each document. make_pipeline(arm) returns the Pipeline for
    that arm.

        learn: corrected examples from exported review data (template → [(document id,
    example)]); each document uses examples from other documents only (leave-one-out)."""
    results = {arm: ArmResult(arm) for arm in ARMS}
    for doc in docs:
        template = doc.load()
        for arm in arms:
            if arm != "A" and doc.pdf is None:
                continue  # Image only: run arm A only.
            pipeline = make_pipeline(arm)
            if learn is not None:
                others = [
                    lesson
                    for owner, lesson in learn.get(template.name, [])
                    if not doc.id.startswith(owner[:8])
                ]
                pipeline = replace(pipeline, lessons=lambda _name, others=others: others)
            path = _photo_of(doc, scratch) if arm == "A" else doc.pdf
            assert path is not None  # noqa: S101
            pdf_read = "text" if arm == "E" else "image"
            for drop in (False, True) if oracle is not None else (False,):
                if oracle is not None:
                    oracle.truth, oracle.drop = doc.truth, drop
                progress(f"{doc.id} · {arm} {ARMS[arm]}{' · 去掉答案' if drop else ''}")
                started = time.perf_counter()
                try:
                    reports = pipeline.run(path, template=template, split="one", pdf_read=pdf_read)
                except Exception as err:  # A failure in one document does not affect the others.
                    results[arm].errors.append(f"{doc.id}: {render(str(err))}")
                    continue
                pages = max(1, sum((r.last_page or r.page) - r.page + 1 for r in reports))
                if not drop:
                    results[arm].seconds_per_page.append((time.perf_counter() - started) / pages)
                report = reports[0]
                for name, gold in doc.truth.items():
                    value, flagged, aligned = _value_of(report, name)
                    if drop:
                        found = aligned is not None and any(
                            same(_type_of(template, name), c.value or c.text, gold)
                            for c in aligned.candidates
                        )
                        for o in results[arm].fields:
                            if o.doc == doc.id and o.name == name:
                                o.recovered = found
                        continue
                    results[arm].fields.append(
                        FieldOutcome(
                            doc.id,
                            arm,
                            name,
                            _key(template, name),
                            same(_type_of(template, name), value, gold),
                            flagged,
                            value=None if value is None else str(value),
                            gold=gold,
                        )
                    )
    return results


def save_part(results: dict[str, ArmResult], path: Path) -> None:
    """Store the results of a partial run (some documents, some arms); merge_parts combines them at
    the end."""
    data = {arm: asdict(r) for arm, r in results.items() if r.fields or r.errors}
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")


def merge_parts(paths: Sequence[Path]) -> dict[str, ArmResult]:
    results = {arm: ArmResult(arm) for arm in ARMS}
    for path in paths:
        for arm, r in json.loads(path.read_text("utf-8")).items():
            results[arm].fields += [FieldOutcome(**o) for o in r["fields"]]
            results[arm].seconds_per_page += r["seconds_per_page"]
            results[arm].errors += r["errors"]
    return results


def _rate(xs: Sequence[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def _percentile(xs: Sequence[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def per_doc_accuracy(result: ArmResult) -> dict[str, float]:
    by: dict[str, list[bool]] = {}
    for o in result.fields:
        by.setdefault(o.doc, []).append(o.right)
    return {d: sum(v) / len(v) for d, v in by.items()}


def paired_interval(
    a: dict[str, float], b: dict[str, float], rounds: int = 2000, seed: int = 0
) -> tuple[float, float, float] | None:
    """Mean difference b − a with a document-paired bootstrap: (mean difference, one-sided 95% lower
    bound, upper bound)."""
    docs = sorted(set(a) & set(b))
    if not docs:
        return None
    diffs = [b[d] - a[d] for d in docs]
    rng = random.Random(seed)  # noqa: S311  Statistical resampling, not cryptography.
    means = sorted(statistics.fmean(rng.choice(diffs) for _ in diffs) for _ in range(rounds))
    return statistics.fmean(diffs), means[int(0.05 * rounds)], means[int(0.95 * rounds) - 1]


def flips(before: ArmResult, after: ArmResult) -> tuple[int, int]:
    """(Number correct before and wrong after, number wrong before and correct after)."""
    old = {(o.doc, o.name): o.right for o in before.fields}
    worse = better = 0
    for o in after.fields:
        was = old.get((o.doc, o.name))
        if was is True and not o.right:
            worse += 1
        elif was is False and o.right:
            better += 1
    return worse, better


def summary(results: dict[str, ArmResult], docs: Sequence[PairDoc], mode: str) -> dict[str, Any]:
    rows = []
    for arm, r in results.items():
        keys = [o for o in r.fields if o.key]
        recovered = [o.recovered for o in r.fields if o.recovered is not None]
        rows.append(
            {
                "arm": arm,
                "name": ARMS[arm],
                "fields": len(r.fields),
                "accuracy": _rate([o.right for o in r.fields]),
                "key_accuracy": _rate([o.right for o in keys]),
                "flagged": _rate([o.flagged for o in r.fields]),
                "silent_wrong": sum(1 for o in r.fields if not o.right and not o.flagged),
                "recovered": _rate([bool(x) for x in recovered]) if recovered else None,
                "p50": _percentile(r.seconds_per_page, 0.5),
                "p95": _percentile(r.seconds_per_page, 0.95),
                "errors": r.errors,
            }
        )
    a, b, d = results["A"], results["B"], results["D"]
    interval = paired_interval(per_doc_accuracy(a), per_doc_accuracy(d))
    return {
        "mode": mode,
        "time": datetime.now().isoformat(timespec="seconds"),
        "docs": [
            {"id": x.id, "kind": x.kind, "template": x.template, "fields": len(x.truth)}
            for x in docs
        ],
        "arms": rows,
        "photo_to_pdf": dict(zip(("worse", "better"), flips(a, d), strict=True)),
        "ocr_to_fused": dict(zip(("worse", "better"), flips(b, d), strict=True)),
        "d_minus_a": None
        if interval is None
        else {"mean": interval[0], "lower95": interval[1], "upper95": interval[2]},
        "outcomes": [asdict(o) for r in results.values() for o in r.fields],
    }


def to_markdown(s: dict[str, Any]) -> str:
    out = io.StringIO()
    mode = "标准答案当回答（oracle，不用模型）" if s["mode"] == "oracle" else "真的用模型抽"
    out.write(f"# 成对测评（{s['time']}）\n\n方式：{mode}；文档 {len(s['docs'])} 份：")
    out.write(
        "、".join(f"{d['id']}（{d['kind']}，{d['fields']} 个字段）" for d in s["docs"]) + "\n\n"
    )
    out.write(
        "| 读法 | 字段准确率 | 关键字段 | 标黄 | 错了没标黄 | 漏读找回 | 每页 P50 | 每页 P95 |\n"
    )
    out.write("|---|---|---|---|---|---|---|---|\n")
    for r in s["arms"]:
        p50 = "—" if r["p50"] is None else f"{r['p50']:.1f}s"
        p95 = "—" if r["p95"] is None else f"{r['p95']:.1f}s"
        out.write(
            f"| {r['arm']} {r['name']} | {_pct(r['accuracy'])} | {_pct(r['key_accuracy'])} | "
            f"{_pct(r['flagged'])} | {r['silent_wrong']} | {_pct(r['recovered'])} | "
            f"{p50} | {p95} |\n"
        )
    w, b = s["photo_to_pdf"]["worse"], s["photo_to_pdf"]["better"]
    out.write(f"\n- 照片对 → PDF（D）错：{w} 个；照片错 → PDF 对：{b} 个\n")
    w, b = s["ocr_to_fused"]["worse"], s["ocr_to_fused"]["better"]
    out.write(f"- 只用 OCR（B）对 → 加核对（D）错：{w} 个；B 错 → D 对：{b} 个\n")
    iv = s["d_minus_a"]
    if iv is not None:
        out.write(
            f"- D − A 字段准确率（按文档配对重采样）：平均 {iv['mean']:+.1%}，"
            f"单侧 95% 下界 {iv['lower95']:+.1%}\n"
        )
        if len(s["docs"]) < 10:
            out.write(f"  - 只有 {len(s['docs'])} 份文档，这个区间说明不了什么，要多收集成对样本\n")
    errors = [e for r in s["arms"] for e in r["errors"]]
    if errors:
        out.write("\n出错：\n" + "".join(f"- {e}\n" for e in errors))
    wrong = [o for o in s["outcomes"] if not o["right"]]
    if wrong:
        out.write(
            "\n## 错的字段\n\n| 文档 | 读法 | 字段 | 给的值 | 标准答案 | 标黄了 |\n"
            "|---|---|---|---|---|---|\n"
        )
        for o in wrong:
            given = "（空）" if o.get("value") is None else o["value"]
            out.write(
                f"| {o['doc']} | {o['arm']} | {o['name']} | {given} | {o.get('gold', '')} | "
                f"{'是' if o['flagged'] else '否'} |\n"
            )
    return out.getvalue()


def write_summary(s: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")
    md = out_dir / "summary.md"
    md.write_text(to_markdown(s), "utf-8")
    return md


def pipeline_factory(ocr: Any, extractor: Any, structure: Any) -> Callable[[str], Any]:
    """Pipeline per arm: B and E without cross-checking, C with cross-checking but no zoom, A and D
    with both."""
    from jacara.pipeline.run import Pipeline

    def make(arm: str) -> Any:
        base = Pipeline(ocr=ocr, extractor=extractor, structure=structure)
        if arm in ("B", "E"):
            return replace(base, field_check=False, zoom=False)
        if arm == "C":
            return replace(base, zoom=False)
        return base

    return make
