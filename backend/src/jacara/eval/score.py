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
- 宽松准确率：文字只差一两个字母（多半是 OCR 认错字，如 LJAN / LIAN）也算对。
  和准确率的差距 = OCR 错字的影响；剩下的错才是"抽错了"（选错了值、多抄少抄）
"""

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
    """宽松地看对不对：文字相似度 ≥ 90%（只差 OCR 错字）。数字和日期和 correct 一样。"""

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


def close_enough(field_type: str, predicted: str | float | None, gold: str) -> bool:
    """宽松比较：文字只看字母和数字，相似度 ≥ 90% 就算对；其他类型和 same() 一样。"""
    if field_type != "string" or not gold or predicted is None:
        return same(field_type, predicted, gold)
    a, b = _letters(str(predicted)), _letters(gold)
    return SequenceMatcher(None, a, b).ratio() >= CLOSE_RATIO


CLOSE_RATIO = 0.9


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
                close=close_enough(aligned.field.type, value, answers[name]),
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
    """SROIE 的日期写法很多，读法和审核时"点原文填值"一样（core/textvalue.py）。"""
    return parse_date(text)
