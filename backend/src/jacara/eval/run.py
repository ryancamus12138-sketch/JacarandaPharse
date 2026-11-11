"""跑一次评测：每张样本走一遍流水线，打分，写出结果文件。

结果放在 results/eval/<时间>/（results/ 在 .gitignore 里）：
    fields.jsonl   每个字段一行：标准答案、模型的值、对不对、有没有出处、标没标黄
    summary.json   汇总指标和这次评测的条件（模型、提示词版本、样本数）
    summary.md     给人看的表格，可以直接贴到 docs/eval/ 里
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jacara.eval.score import FieldScore, Summary, score_report, summarize
from jacara.eval.sroie import Sample
from jacara.plugins.exporters import DocumentReport

Process = Callable[[Path], list[DocumentReport]]
"""处理一个文件的函数。真跑时是 Pipeline.run，测试里换成假的。"""


@dataclass
class EvalResult:
    scores: list[FieldScore] = field(default_factory=list)
    summaries: list[Summary] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    """样本编号 → 出错原因（模型没响应、图片读不了……）。出错的样本不算进指标。"""
    model: str = ""
    prompt_version: str = ""
    seconds: float = 0.0


def run_eval(
    samples: Sequence[Sample],
    process: Process,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> EvalResult:
    result = EvalResult()
    start = time.monotonic()
    for i, sample in enumerate(samples, start=1):
        try:
            reports = process(sample.image)
        except Exception as err:  # 一张出错不影响其他样本，原因记下来
            result.failures[sample.sample_id] = f"{type(err).__name__}: {err}"
        else:
            report = reports[0]  # 收据都是一页
            result.model = report.extraction.model
            result.prompt_version = report.extraction.prompt_version
            result.scores += score_report(sample.sample_id, sample.answers, report)
        if on_progress:
            on_progress(i, len(samples), sample.sample_id)
    result.summaries = summarize(result.scores)
    result.seconds = time.monotonic() - start
    return result


def write_result(result: EvalResult, out_dir: Path, dataset: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "fields.jsonl").open("w", encoding="utf-8") as f:
        for s in result.scores:
            f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    meta = {
        "dataset": dataset,
        "samples": len({s.sample_id for s in result.scores}) + len(result.failures),
        "failed": len(result.failures),
        "model": result.model,
        "prompt_version": result.prompt_version,
        "seconds": round(result.seconds, 1),
    }
    summary = {
        **meta,
        "fields": [s.to_dict() for s in result.summaries],
        "failures": result.failures,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(to_markdown(result, meta), encoding="utf-8")


def to_markdown(result: EvalResult, meta: dict[str, object]) -> str:
    head = (
        f"数据集 {meta['dataset']} · {meta['samples']} 张（失败 {meta['failed']}）· "
        f"模型 {meta['model']} · 提示词 {meta['prompt_version']} · 用时 {meta['seconds']} 秒\n\n"
    )
    headers = ["字段", "数量", "准确率", "宽松准确率", "出处覆盖率"]
    headers += ["错值拦截率", "误报率", "放行率", "放行准确率"]
    table = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for s in result.summaries:
        cells = [
            s.field,
            str(s.total),
            _pct(s.accuracy),
            _pct(s.close_accuracy),
            _pct(s.evidence_rate),
            _pct(s.catch_rate),
            _pct(s.false_alarm_rate),
            _pct(s.pass_rate),
            _pct(s.pass_accuracy),
        ]
        table.append("| " + " | ".join(cells) + " |")
    note = (
        "\n\n不找出处、直接用模型的值时，错值拦截率是 0%，放行准确率等于准确率。"
        "两者的差距就是“找出处 + 规则校验”的价值。\n"
    )
    return head + "\n".join(table) + note


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"
