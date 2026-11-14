"""表格明细的回放比较（ADR 0062）：人审核后的行和抽出来的行先一一配对，再逐格比。

做法照 DocILE 基准的"明细识别"（Line Item Recognition，LIR）评测（Šimsa 等，ICDAR 2023，
arXiv:2302.05658，第 4.2 节）：

1. 每一对（人审核后的行，抽出来的行）先逐格比，数这一对里"对了的格"有几个；
2. 在所有配对方式里，找让"对了的格"总数最多的那一种（最大匹配，使总召回最高）；
3. 指标是所有明细格子上的 micro F1。

配对用 scipy.optimize.linear_sum_assignment（maximize=True）求解。
replay.py 用到这里时在函数里导入（这里要用 replay 的 Pred、outcome，顶层互相导入会循环）。
一个格子也没对上的配对不算配上：
那一行人审核后的格子算漏、抽出来的格子算多（这样分和算成"错"，P、R、F1 完全一样，但更好读）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from jacara.eval.replay import Outcome, Pred, outcome

TruthRow = Mapping[str, tuple[str, str | None]]
"""人审核后的一行：列名 → (类型, 值)。"""

PredRow = Mapping[str, Pred]
"""抽出来的一行：列名 → 预测。"""


@dataclass(frozen=True)
class CellResult:
    """人审核后的一格，在某一边的结果。"""

    table: str
    row: int
    """人审核后第几行（从 1 开始，按显示顺序）。"""
    column: str
    type: str
    truth: str | None
    pred: Pred
    outcome: Outcome
    matched_row: int | None
    """配上的抽出来的那一行（从 1 开始）；没配上是 None。"""


@dataclass(frozen=True)
class TableSide:
    """一张表在一边的结果：人审核后每一格的结果，加上多出来的格子数。"""

    cells: list[CellResult]
    extra_cells: int
    """抽出来的、没有对应的人审核格子的非空格（多出来的行，或配上的行里人那边没有的列）。"""
    extra_rows: int
    """抽出来的行里没配上的行数。"""


def _score(truth: TruthRow, pred: PredRow) -> int:
    """一对行里对了的格子数（人那边是空的格子不算）。"""
    return sum(
        outcome(type_, value, pred[column].value) == "correct"
        for column, (type_, value) in truth.items()
        if column in pred and value is not None and value.strip()
    )


def match_rows(truth: Sequence[TruthRow], pred: Sequence[PredRow]) -> dict[int, int]:
    """人审核后的行号（下标）→ 抽出来的行号（下标）。只留下至少对了一格的配对。"""
    if not truth or not pred:
        return {}
    score = np.array([[_score(t, p) for p in pred] for t in truth], dtype=np.int64)
    rows, cols = linear_sum_assignment(score, maximize=True)
    return {int(r): int(c) for r, c in zip(rows, cols, strict=True) if score[r, c] > 0}


def compare_table(table: str, truth: Sequence[TruthRow], pred: Sequence[PredRow]) -> TableSide:
    pairs = match_rows(truth, pred)
    cells: list[CellResult] = []
    extra = 0
    for i, row in enumerate(truth):
        j = pairs.get(i)
        got = pred[j] if j is not None else {}
        for column, (type_, value) in row.items():
            p = got.get(column, Pred(None, False))
            cells.append(
                CellResult(
                    table,
                    i + 1,
                    column,
                    type_,
                    value,
                    p,
                    outcome(type_, value, p.value),
                    j + 1 if j is not None else None,
                )
            )
        extra += sum(1 for column, p in got.items() if column not in row and _filled(p.value))
    matched = set(pairs.values())
    for j, row in enumerate(pred):
        if j not in matched:
            extra += sum(1 for p in row.values() if _filled(p.value))
    return TableSide(cells, extra, len(pred) - len(matched))


def _filled(value: str | None) -> bool:
    return value is not None and bool(value.strip())


@dataclass(frozen=True)
class Micro:
    """所有明细格子上的 micro 指标（DocILE LIR 的主指标是 micro F1）。"""

    correct: int
    wrong: int
    missing: int
    extra: int

    @property
    def precision(self) -> float:
        guessed = self.correct + self.wrong + self.extra
        return self.correct / guessed if guessed else 0.0

    @property
    def recall(self) -> float:
        expected = self.correct + self.wrong + self.missing
        return self.correct / expected if expected else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def micro(sides: Sequence[TableSide]) -> Micro:
    counts = {"correct": 0, "wrong": 0, "missing": 0, "extra": 0}
    for side in sides:
        for cell in side.cells:
            if cell.outcome != "empty":
                counts[cell.outcome] += 1
        counts["extra"] += side.extra_cells
    return Micro(**counts)
