"""打分：模型的值对不对、有没有出处、错的有没有被标出来。

只看三件事，每个字段一行：
- correct：和标准答案一样吗（先统一格式再比）
- has_evidence：在原文里找到出处了吗
- flagged：标黄了吗（找不到出处、只是相似、或者规则不通过），和审核页面的标黄一模一样

由这三列算出的指标：
- 准确率：模型直接给的值有多少是对的
- 出处覆盖率：有多少值在原文里找到了出处
- 错值拦截率：错的值里，有多少被标黄、交给了人
- 误报率：对的值里，有多少也被标黄了（让人白看一眼）
- 放行率：没标黄、直接放行的值占多少（省下的人工）
- 放行准确率：直接放行的值有多准——用户能不能信任"没标黄的就是对的"，看的就是它
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date

from jacara.plugins.exporters import DocumentReport

_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}


@dataclass(frozen=True)
class FieldScore:
    sample_id: str
    field: str
    gold: str
    predicted: str | None
    correct: bool
    has_evidence: bool
    flagged: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Summary:
    field: str
    total: int
    accuracy: float
    evidence_rate: float
    catch_rate: float | None
    """错的值一个都没有时，没法算拦截率，是 None。"""
    false_alarm_rate: float | None
    pass_rate: float
    pass_accuracy: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def same(field_type: str, predicted: str | float | None, gold: str) -> bool:
    """模型的值和标准答案是不是同一个东西。标准答案为空时，模型也说"没有"才算对。"""
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
    return _letters(str(predicted)) == _letters(gold)


def score_report(
    sample_id: str, answers: dict[str, str], report: DocumentReport
) -> list[FieldScore]:
    """一张样本的打分：只评标准答案里有的字段。"""
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
            )
        )
    return scores


def summarize(scores: Sequence[FieldScore]) -> list[Summary]:
    """每个字段一行，最后一行是"全部"。"""
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
        evidence_rate=_rate([s for s in scores if s.has_evidence], scores),
        catch_rate=_rate([s for s in wrong if s.flagged], wrong) if wrong else None,
        false_alarm_rate=_rate([s for s in right if s.flagged], right) if right else None,
        pass_rate=_rate(passed, scores),
        pass_accuracy=_rate([s for s in passed if s.correct], passed) if passed else None,
    )


def _rate(part: Iterable[object], whole: Sequence[object]) -> float:
    return len(list(part)) / len(whole) if whole else 0.0


def _letters(text: str) -> str:
    """文字比较：只看字母和数字，忽略大小写、空格和标点。OCR 常把 . 读成 _ 或多读空格。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).upper()


def _parse_number(text: str) -> float | None:
    """把 "RM 1,234.50" 变成 1234.5。"""
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits)
    except ValueError:
        return None


def _parse_date(text: str) -> date | None:
    """SROIE 的日期写法很多：25/12/2018、15-01-19、02 MAR 2018、OCT 3, 2016、20180304。

    马来西亚一般是 日/月/年；月份大于 12 时说明这张是 月/日/年，就对调。
    """
    t = text.strip().upper()
    try:
        if m := re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t):
            day, month = int(m[1]), int(m[2])
            if month > 12:
                day, month = month, day
            return date(_year(m[3]), month, day)
        if m := re.fullmatch(r"(\d{8})", t):
            s = m[1]
            if s[:2] in ("19", "20"):
                return date(int(s[:4]), int(s[4:6]), int(s[6:]))
            return date(int(s[4:]), int(s[2:4]), int(s[:2]))
        if m := re.search(r"(\d{1,2})\s*[-/]?\s*([A-Z]{3})[A-Z]*\s*[-/,]?\s*(\d{2,4})", t):
            month = _MONTHS.get(m[2])
            return date(_year(m[3]), month, int(m[1])) if month else None
        if m := re.search(r"([A-Z]{3})[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{2,4})", t):
            month = _MONTHS.get(m[1])
            return date(_year(m[3]), month, int(m[2])) if month else None
    except ValueError:
        return None
    return None


def _year(text: str) -> int:
    return int(text) + 2000 if len(text) == 2 else int(text)
