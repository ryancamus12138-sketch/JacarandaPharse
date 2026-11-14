"""Replay comparison of table line items: reviewed rows are paired with extracted rows, then
compared cell by cell.

Follows the Line Item Recognition (LIR) evaluation of the DocILE benchmark (Šimsa et al., ICDAR
2023, arXiv:2302.05658, section 4.2):

1. For each pair (reviewed row, extracted row), compare cell by cell and count the correct cells;
2. Among all pairings, choose the one with the largest total of correct cells (maximum matching,
   maximizing overall recall);
3. The metric is micro F1 over all line-item cells.

The pairing is solved with scipy.optimize.linear_sum_assignment (maximize=True). replay.py
imports this module inside functions (this module uses Pred and outcome from replay; top-level
imports would be circular). A pair without a single correct cell is not considered matched: the
reviewed cells of that row count as missed and the extracted cells as spurious. Precision, recall
and F1 are identical to counting them as wrong, but the result is easier to read."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from jacara.eval.replay import Outcome, Pred, outcome

TruthRow = Mapping[str, tuple[str, str | None]]
"""One reviewed row: column name → (type, value)."""

PredRow = Mapping[str, Pred]
"""One extracted row: column name → prediction."""


@dataclass(frozen=True)
class CellResult:
    """Outcome of one reviewed cell in one arm."""

    table: str
    row: int
    """Reviewed row number (1-based, display order)."""
    column: str
    type: str
    truth: str | None
    pred: Pred
    outcome: Outcome
    matched_row: int | None
    """Matched extracted row (1-based); None if unmatched."""


@dataclass(frozen=True)
class TableSide:
    """Result of one table in one arm: the outcome of every reviewed cell plus the number of
    spurious cells."""

    cells: list[CellResult]
    extra_cells: int
    """Non-empty extracted cells with no corresponding reviewed cell (unmatched rows, or columns in
    matched rows that are empty on the reviewed side)."""
    extra_rows: int
    """Number of unmatched extracted rows."""


def _score(truth: TruthRow, pred: PredRow) -> int:
    """Number of correct cells in a row pair (cells empty on the reviewed side are not counted)."""
    return sum(
        outcome(type_, value, pred[column].value) == "correct"
        for column, (type_, value) in truth.items()
        if column in pred and value is not None and value.strip()
    )


def match_rows(truth: Sequence[TruthRow], pred: Sequence[PredRow]) -> dict[int, int]:
    """Reviewed row index → extracted row index. Only pairs with at least one correct cell are
    kept."""
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
    """Micro metrics over all line-item cells (the primary DocILE LIR metric is micro F1)."""

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
