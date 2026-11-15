# ruff: noqa: E501
"""OCR model evaluation: compare two recognition (or detection) models on one split of a library
dataset.

- Recognition: only recognition runs on each line crop. Metrics follow TextRecMetric (the
  recognition evaluation tool by SWHL, author of RapidOCR; Apache-2.0): Exact Match (share of
  lines identical to the label) and Char Match (1 − mean normalized edit distance, NL =
  Levenshtein(pred, label) / max(len(pred), len(label))). Lines that differ only in whitespace,
  letter case or full-width/half-width forms are counted separately (the format error label).
- Detection: only detection runs on each full-page image. Precision, Recall and H-mean follow
  DetectionIoUEvaluator in PaddleOCR ppocr/metrics/eval_det_iou.py (iou_constraint=0.5,
  area_precision_constraint=0.5).
- The two models are paired per item: fixed, broken, exact McNemar test; for recognition,
  additionally a bootstrap grouped by business document (as in replay).

Read-only: neither the model selection nor the dataset is modified. Used by the CLI (`jacara eval
ocr`) and by the background task started from the web UI."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from jacara.eval.replay import (
    EVIDENCE_TEXT,
    Outcome,
    evidence_level,
    group_bootstrap,
    mcnemar_exact,
)
from jacara.modules.hub.datasets import Box, Item, Parsed, images
from jacara.modules.ocr_samples.crop import error_tags

Task = Literal["rec", "det"]


class EvalEngine(Protocol):
    def read_line(self, image: Path) -> tuple[str, float] | None: ...

    def detect(self, image: Path) -> list[list[tuple[float, float]]]: ...


@dataclass(frozen=True)
class EvalArm:
    """Model of one arm. Paths are relative to the project root; None means the model bundled with
    RapidOCR."""

    name: str
    label: str
    det: str | None = None
    rec: str | None = None
    dict_path: str | None = None
    """Recognition dictionary."""
    sha256: dict[str, str] = field(default_factory=dict)


# ---- Recognition ----


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insertion, deletion and substitution cost 1), two-row dynamic programming."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalized_distance(pred: str, truth: str) -> float:
    """NL from TextRecMetric: edit distance divided by the length of the longer string; 0 when both
    are empty."""
    longest = max(len(pred), len(truth))
    return levenshtein(pred, truth) / longest if longest else 0.0


@dataclass
class RecLine:
    image: str
    group: str
    truth: str
    a: str
    b: str

    @property
    def a_outcome(self) -> Outcome:
        return "correct" if self.a == self.truth else "wrong"

    @property
    def b_outcome(self) -> Outcome:
        return "correct" if self.b == self.truth else "wrong"


def rec_side(lines: Sequence[RecLine], arm: Literal["a", "b"]) -> dict[str, Any]:
    preds = [(line.a if arm == "a" else line.b, line.truth) for line in lines]
    n = len(preds)
    exact = sum(p == t for p, t in preds)
    format_only = sum(p != t and error_tags(p, t) == ["format"] for p, t in preds)
    char = 1 - sum(normalized_distance(p, t) for p, t in preds) / n if n else 0.0
    return {
        "lines": n,
        "exact": exact,
        "exact_match": round(exact / n, 4) if n else 0.0,
        "char_match": round(char, 4),
        "format_only": format_only,
        "empty": sum(not p for p, _ in preds),
    }


def summarize_rec(lines: Sequence[RecLine], grouped: bool) -> dict[str, Any]:
    fixed = sum(r.a_outcome == "wrong" and r.b_outcome == "correct" for r in lines)
    broken = sum(r.a_outcome == "correct" and r.b_outcome == "wrong" for r in lines)
    groups = {r.group for r in lines}
    ci = group_bootstrap(lines)
    level = evidence_level(len(groups), len(lines))
    return {
        "task": "rec",
        "a": rec_side(lines, "a"),
        "b": rec_side(lines, "b"),
        "fixed": fixed,
        "broken": broken,
        "mcnemar_p": round(mcnemar_exact(fixed, broken), 4),
        "bootstrap_ci": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
        "groups": len(groups),
        "grouped": grouped,
        "evidence": level,
        "evidence_text": EVIDENCE_TEXT[level],
    }


# ---- Detection: PaddleOCR DetectionIoUEvaluator ----

IOU_CONSTRAINT = 0.5
AREA_PRECISION_CONSTRAINT = 0.5


@dataclass
class DetImage:
    """Detection result of one image for one arm."""

    gt_care: int
    det_care: int
    matched: int
    gt_hit: list[bool]
    """Whether each ground-truth box (in original order) was matched; ### boxes are False and not
    counted."""


def evaluate_image(gt: Sequence[Box], det: Sequence[Sequence[tuple[float, float]]]) -> DetImage:
    """Follows evaluate_image in PaddleOCR eval_det_iou.py:

    - Invalid polygons are skipped; ground-truth boxes labeled ### are don't-care;
    - A detection whose intersection with a don't-care box exceeds half of its own area is
      also don't-care;
    - Pairs are matched in order where IoU > 0.5, both sides are unmatched and neither is
      don't-care."""
    from shapely.geometry import Polygon

    gt_polys: list[Any] = []
    gt_dont: set[int] = set()
    gt_index: list[int] = []
    for n, box in enumerate(gt):
        poly = Polygon(box.points)
        if not poly.is_valid:
            continue
        if box.ignore:
            gt_dont.add(len(gt_polys))
        gt_polys.append(poly)
        gt_index.append(n)
    det_polys: list[Any] = []
    det_dont: set[int] = set()
    for points in det:
        poly = Polygon(points)
        if not poly.is_valid:
            continue
        if gt_dont and poly.area > 0:
            for d in gt_dont:
                if gt_polys[d].intersection(poly).area / poly.area > AREA_PRECISION_CONSTRAINT:
                    det_dont.add(len(det_polys))
                    break
        det_polys.append(poly)
    gt_matched = [False] * len(gt_polys)
    det_matched = [False] * len(det_polys)
    matched = 0
    for g, gp in enumerate(gt_polys):
        for d, dp in enumerate(det_polys):
            if gt_matched[g] or det_matched[d] or g in gt_dont or d in det_dont:
                continue
            union = gp.union(dp).area
            iou = gp.intersection(dp).area / union if union else 0.0
            if iou > IOU_CONSTRAINT:
                gt_matched[g] = det_matched[d] = True
                matched += 1
    hit = [False] * len(gt)
    for g, n in enumerate(gt_index):
        hit[n] = gt_matched[g]
    return DetImage(len(gt_polys) - len(gt_dont), len(det_polys) - len(det_dont), matched, hit)


def det_metrics(results: Sequence[DetImage]) -> dict[str, Any]:
    """combine_results: Precision, Recall and H-mean over the totals of all images."""
    gt = sum(r.gt_care for r in results)
    det = sum(r.det_care for r in results)
    matched = sum(r.matched for r in results)
    recall = matched / gt if gt else 0.0
    precision = matched / det if det else 0.0
    hmean = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "images": len(results),
        "gt_boxes": gt,
        "det_boxes": det,
        "matched": matched,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "hmean": round(hmean, 4),
    }


@dataclass
class DetLine:
    image: str
    group: str
    a: DetImage
    b: DetImage
    ignore: list[bool]


def summarize_det(lines: Sequence[DetLine]) -> dict[str, Any]:
    fixed = broken = 0
    for line in lines:
        for a_hit, b_hit, ignored in zip(line.a.gt_hit, line.b.gt_hit, line.ignore, strict=True):
            if ignored:
                continue
            fixed += b_hit and not a_hit
            broken += a_hit and not b_hit
    groups = {line.group for line in lines}
    boxes = sum(line.a.gt_care for line in lines)
    level = evidence_level(len(groups), boxes)
    return {
        "task": "det",
        "a": det_metrics([line.a for line in lines]),
        "b": det_metrics([line.b for line in lines]),
        "fixed": fixed,
        "broken": broken,
        "mcnemar_p": round(mcnemar_exact(fixed, broken), 4),
        "groups": len(groups),
        "evidence": level,
        "evidence_text": EVIDENCE_TEXT[level],
    }


# ---- Run ----


@dataclass
class EvalResult:
    task: Task
    summary: dict[str, Any]
    lines: list[dict[str, Any]]


def run(
    data: bytes,
    parsed: Parsed,
    split: str,
    arms: tuple[EvalArm, EvalArm],
    engines: Callable[[EvalArm], EvalEngine],
    progress: Callable[[str], None] = lambda _: None,
) -> EvalResult:
    """Run both models on the given split of the dataset. engines(arm) returns the OCR engine of
    that arm."""
    items = parsed.split(split)
    if not items:
        raise ValueError(f"数据集里没有 {split} 这一边")
    task: Task = "rec" if parsed.kind == "ocr_rec" else "det"
    a_engine, b_engine = engines(arms[0]), engines(arms[1])
    started = time.perf_counter()
    rec_lines: list[RecLine] = []
    det_lines: list[DetLine] = []
    with tempfile.TemporaryDirectory() as tmp:
        for n, (item, picture) in enumerate(images(data, items), start=1):
            if n == 1 or n % 20 == 0 or n == len(items):
                progress(f"{n}/{len(items)}")
            path = Path(tmp) / f"img{Path(item.image).suffix or '.png'}"
            path.write_bytes(picture)
            group = item.group or f"img:{item.image}"
            if task == "rec":
                rec_lines.append(
                    RecLine(
                        item.image,
                        group,
                        item.text or "",
                        _text(a_engine.read_line(path)),
                        _text(b_engine.read_line(path)),
                    )
                )
            else:
                det_lines.append(
                    DetLine(
                        item.image,
                        group,
                        evaluate_image(item.boxes, a_engine.detect(path)),
                        evaluate_image(item.boxes, b_engine.detect(path)),
                        [b.ignore for b in item.boxes],
                    )
                )
    grouped = any(i.group for i in items)
    summary = summarize_rec(rec_lines, grouped) if task == "rec" else summarize_det(det_lines)
    summary |= {
        "split": split,
        "arms": [asdict(a) for a in arms],
        "seconds": round(time.perf_counter() - started, 1),
    }
    lines = (
        [asdict(r) | {"a_ok": r.a == r.truth, "b_ok": r.b == r.truth} for r in rec_lines]
        if task == "rec"
        else [
            {
                "image": d.image,
                "group": d.group,
                "a": asdict(d.a),
                "b": asdict(d.b),
            }
            for d in det_lines
        ]
    )
    return EvalResult(task, summary, lines)


def _text(result: tuple[str, float] | None) -> str:
    return result[0] if result else ""


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def summary_markdown(s: dict[str, Any], dataset: str) -> str:
    a_label, b_label = (arm["label"] for arm in s["arms"])
    head = [
        "# OCR 模型评测",
        "",
        f"- 数据集：{dataset}，{s['split']} 这一边",
        f"- A：{a_label}；B：{b_label}",
        f"- 证据：{s['evidence_text']}",
        "",
    ]
    a, b = s["a"], s["b"]
    if s["task"] == "rec":
        ci = s["bootstrap_ci"]
        body = [
            "| | A | B |",
            "|---|---|---|",
            f"| Exact Match（整行一模一样） | {_pct(a['exact_match'])} | {_pct(b['exact_match'])} |",
            f"| Char Match（1 − 平均归一化编辑距离） | {_pct(a['char_match'])} | {_pct(b['char_match'])} |",
            f"| 只差空格、大小写、全角半角 | {a['format_only']} | {b['format_only']} |",
            f"| 什么也没认出来 | {a['empty']} | {b['empty']} |",
            "",
            f"- {a['lines']} 行、{s['groups']} 组"
            + ("" if s["grouped"] else "（没有业务单据组号，每张图自成一组，区间偏窄）"),
            f"- B 修好了 **{s['fixed']}** 行、弄坏了 **{s['broken']}** 行（精确 McNemar p = {s['mcnemar_p']}）",
            (
                f"- Exact Match 差（B − A）分组 bootstrap 的 95% 区间：{_pct(ci[0])} ～ {_pct(ci[1])}"
                if ci
                else "- 不到 2 组，不做 bootstrap"
            ),
        ]
    else:
        body = [
            "| | A | B |",
            "|---|---|---|",
            f"| Precision | {_pct(a['precision'])} | {_pct(b['precision'])} |",
            f"| Recall | {_pct(a['recall'])} | {_pct(b['recall'])} |",
            f"| H-mean | {_pct(a['hmean'])} | {_pct(b['hmean'])} |",
            f"| 检测框 / 配上的 | {a['det_boxes']} / {a['matched']} | {b['det_boxes']} / {b['matched']} |",
            "",
            f"- {a['images']} 张图、{a['gt_boxes']} 个标注框（### 的不算）",
            f"- B 新配上 **{s['fixed']}** 个标注框、丢了 **{s['broken']}** 个（精确 McNemar p = {s['mcnemar_p']}）",
            "- IoU > 0.5 算配上，照 PaddleOCR 的 DetectionIoUEvaluator",
        ]
    return "\n".join([*head, *body, "", f"用时 {s['seconds']} 秒。", ""])


def write_result(result: EvalResult, out: Path, dataset: str) -> Path:
    """summary.md, summary.json, lines.jsonl (business data; kept on this machine only)."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out / "lines.jsonl").open("w", encoding="utf-8") as f:
        for line in result.lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    md = out / "summary.md"
    md.write_text(summary_markdown(result.summary, dataset), encoding="utf-8")
    return md


def lines_jsonl(result: EvalResult) -> bytes:
    return "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in result.lines).encode("utf-8")


def default_engine(arm: EvalArm) -> EvalEngine:
    """Real RapidOCR with the same settings as regular processing (confidence threshold, longer
    side); only the model files differ."""
    from jacara.core import config
    from jacara.plugins.ocr import OcrConfig, RapidOcrEngine

    settings = config.get_settings()

    def path(value: str | None) -> Path | None:
        return config.data_home() / value if value else None

    return RapidOcrEngine(
        OcrConfig(
            max_side=settings.jacara_ocr_max_side,
            det_model=path(arm.det),
            rec_model=path(arm.rec),
            rec_dict=path(arm.dict_path),
        )
    )


__all__ = [
    "Box",
    "EvalArm",
    "EvalEngine",
    "EvalResult",
    "Item",
    "default_engine",
    "evaluate_image",
    "levenshtein",
    "normalized_distance",
    "run",
    "summary_markdown",
    "write_result",
]
