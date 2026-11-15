"""Scoring: whether each model value is correct, has evidence, and is flagged when wrong.

Three columns per field:
- correct: equals the ground truth (after normalization)
- has_evidence: located in the source text
- flagged: highlighted for review (no evidence, only a fuzzy match, or a failed rule), exactly as
  on the review page

Metrics derived from these columns:
- Accuracy: share of model values that are correct
- Evidence coverage: share of values located in the source text
- Error interception rate: share of wrong values that are flagged for review
- False alarm rate: share of correct values that are flagged anyway (wasted review effort)
- Pass-through rate: share of values passed without a flag (manual effort saved)
- Pass-through accuracy: how accurate unflagged values are; this decides whether users can trust
  that unflagged values are correct
- Lenient accuracy: text differing by one or two characters (usually OCR misreads such as LJAN /
  LIAN) also counts as correct. The gap to accuracy is the impact of OCR errors; the remaining
  errors are extraction errors (wrong value chosen, characters added or dropped)"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher

from jacara.core.textvalue import parse_date
from jacara.plugins.exporters import DocumentReport


@dataclass(frozen=True)
class FieldScore:
    sample_id: str
    field: str
    gold: str
    predicted: str | None
    correct: bool
    has_evidence: bool
    flagged: bool
    close: bool = False
    """Lenient correctness: text similarity ≥ 90% (differs only by OCR misreads). Numbers and dates
    as in correct."""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Summary:
    field: str
    total: int
    accuracy: float
    close_accuracy: float
    evidence_rate: float
    catch_rate: float | None
    """None when there are no wrong values, since the interception rate is undefined."""
    false_alarm_rate: float | None
    pass_rate: float
    pass_accuracy: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def same(field_type: str, predicted: str | float | None, gold: str) -> bool:
    """Whether the model value and the ground truth denote the same thing. When the ground truth is
    empty, only an empty answer is correct."""
    if not gold:
        return predicted is None
    if predicted is None:
        return False
    if field_type == "number":
        expected = _parse_number(gold)
        return expected is not None and abs(float(predicted) - expected) < 0.005
    if field_type == "date":
        expected = _parse_date(gold)
        return expected is not None and expected == _parse_date(str(predicted))
    clock = _clock(str(predicted)), _clock(gold)
    if clock[0] is not None and clock[1] is not None:
        return clock[0] == clock[1]  # 8:13:39 PM and 20:13:39 are the same time.
    return _letters(str(predicted)) == _letters(gold)


_TIME = re.compile(r"^\s*(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?\s*([AaPp]\.?[Mm]\.?)?\s*$")


def _clock(text: str) -> tuple[int, int, int] | None:
    """Time → (hour, minute, second), converting 12-hour to 24-hour clock. None if not a time."""
    m = _TIME.match(text)
    if m is None:
        return None
    hour = int(m[1])
    if m[4]:
        hour = hour % 12 + (12 if m[4][0] in "Pp" else 0)
    return hour, int(m[2]), int(m[3] or 0)


def close_enough(field_type: str, predicted: str | float | None, gold: str) -> bool:
    """Lenient comparison: text compares letters and digits only and matches at similarity ≥ 90%;
    other types as in same()."""
    if field_type != "string" or not gold or predicted is None:
        return same(field_type, predicted, gold)
    a, b = _letters(str(predicted)), _letters(gold)
    return SequenceMatcher(None, a, b).ratio() >= CLOSE_RATIO


CLOSE_RATIO = 0.9


def score_report(
    sample_id: str, answers: dict[str, str], report: DocumentReport
) -> list[FieldScore]:
    """Score one sample: only fields present in the ground truth are evaluated."""
    rule_failed = report.checks.fields_with_issues()
    scores = []
    for aligned in report.aligned:
        name = aligned.field.name
        if name not in answers:
            continue
        value = aligned.field.value
        scores.append(
            FieldScore(
                sample_id=sample_id,
                field=name,
                gold=answers[name],
                predicted=None if value is None else str(value),
                correct=same(aligned.field.type, value, answers[name]),
                has_evidence=aligned.evidence is not None,
                flagged=aligned.needs_review or name in rule_failed,
                close=close_enough(aligned.field.type, value, answers[name]),
            )
        )
    return scores


def summarize(scores: Sequence[FieldScore]) -> list[Summary]:
    """One row per field; the last row is "all"."""
    fields = list(dict.fromkeys(s.field for s in scores))
    rows = [_summary(f, [s for s in scores if s.field == f]) for f in fields]
    if scores:
        rows.append(_summary("全部", scores))
    return rows


def _summary(name: str, scores: Sequence[FieldScore]) -> Summary:
    wrong = [s for s in scores if not s.correct]
    right = [s for s in scores if s.correct]
    passed = [s for s in scores if not s.flagged]
    return Summary(
        field=name,
        total=len(scores),
        accuracy=_rate(right, scores),
        close_accuracy=_rate([s for s in scores if s.correct or s.close], scores),
        evidence_rate=_rate([s for s in scores if s.has_evidence], scores),
        catch_rate=_rate([s for s in wrong if s.flagged], wrong) if wrong else None,
        false_alarm_rate=_rate([s for s in right if s.flagged], right) if right else None,
        pass_rate=_rate(passed, scores),
        pass_accuracy=_rate([s for s in passed if s.correct], passed) if passed else None,
    )


def _rate(part: Iterable[object], whole: Sequence[object]) -> float:
    return len(list(part)) / len(whole) if whole else 0.0


def _letters(text: str) -> str:
    """Text comparison on letters and digits only, ignoring case, whitespace and punctuation. OCR
    often reads . as _ or inserts spaces."""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).upper()


def _parse_number(text: str) -> float | None:
    """Convert "RM 1,234.50" to 1234.5."""
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits)
    except ValueError:
        return None


def _parse_date(text: str) -> date | None:
    """SROIE dates come in many formats; parsed the same way as click-to-fill during review
    (core/textvalue.py)."""
    return parse_date(text)
