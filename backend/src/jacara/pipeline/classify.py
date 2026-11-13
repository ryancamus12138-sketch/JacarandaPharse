"""自动判断单据用哪个模板（ADR 0038）。

上传时选"自动判断"：每张单据从"我的模板"里挑最合适的一个。
1. 先按关键字给每个模板打分：模板的字段名、别名、名字在单据文字里出现了多少（快，不用模型）；
2. 分数最高的几个交给模型，模型看单据开头的文字和模板说明，选一个，或者说都不合适；
3. 都不合适：不用模板，抽出所有键值对（可以拿它建新模板）。
没有模型能问（命令行测试、模型不会选）时，按关键字分数选，分数太低就当都不合适。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import group_rows
from jacara.templates import Template, TemplateField

SHORTLIST = 5
"""交给模型挑的模板最多几个。太多了提示词长、模型也容易挑错。"""

HEAD_LINES = 60
"""给模型看单据开头的几行。种类、抬头、国家一般都在前面。"""

MIN_KEYWORD_SCORE = 0.3
"""没有模型时，关键字分数（对上的字段占几成）至少多少才算合适。"""

Chooser = Callable[[str, Sequence[str]], "tuple[int, str] | None"]
"""问模型：（单据文字，模板说明列表）→（选第几个，从 1 开始，0 是都不合适；理由）。
答不上来返回 None。"""


@dataclass(frozen=True)
class Candidate:
    """一个可以选的模板：我的模板的最新版。"""

    template: Template
    template_id: uuid.UUID
    version_id: uuid.UUID


@dataclass(frozen=True)
class AutoChoice:
    """上传时选了"自动判断"：交给流水线的候选模板。"""

    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Classification:
    picked: Candidate | None
    """选中的模板；都不合适是 None（改成抽出所有键值对）。"""
    reason: str
    by: str
    """model：模型选的；keywords：按关键字选的；none：没有可选的模板。"""
    scores: tuple[tuple[Candidate, float], ...] = ()

    def to_json(self) -> dict[str, Any]:
        picked = self.picked
        return {
            "picked": None
            if picked is None
            else {
                "template_id": str(picked.template_id),
                "version_id": str(picked.version_id),
                "title": picked.template.title,
                "version": picked.template.version,
            },
            "reason": self.reason,
            "by": self.by,
            "candidates": [
                {"template_id": str(c.template_id), "title": c.template.title, "score": round(s, 2)}
                for c, s in self.scores
            ],
        }


def head_text(page: OcrPage, lines: int = HEAD_LINES) -> str:
    """单据开头的几行文字（按视觉行）。"""
    return "\n".join(r.text for r in group_rows(page)[:lines])


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _all_fields(template: Template) -> list[TemplateField]:
    fields = list(template.fields)
    for table in template.tables:
        fields += table.columns
    return fields


def keyword_score(text: str, template: Template) -> float:
    """对上的字段占几成：字段的中文名或任何一个别名在单据文字里出现，就算对上。
    模板名出现在单据上（"增值税发票""Tax Invoice"）再加 0.5。"""
    plain = _squash(text)
    fields = _all_fields(template)
    hits = 0
    for f in fields:
        words = [w for w in (f.label, *f.aliases) if len(_squash(w)) >= 2]
        if any(_squash(w) in plain for w in words):
            hits += 1
    score = hits / len(fields) if fields else 0.0
    if len(_squash(template.title)) >= 2 and _squash(template.title) in plain:
        score += 0.5
    return score


def describe(template: Template) -> str:
    """给模型看的模板说明：名字、说明、领域、国家、要抽的字段。"""
    countries = "、".join(template.countries) or "通用（不分国家）"
    domains = "、".join(template.domains) or "（没写）"
    fields = "、".join(f.label for f in _all_fields(template)[:30])
    description = template.description.strip().replace("\n", " ")[:200]
    return (
        f"{template.title}：{description}\n   领域：{domains}；国家和地区：{countries}\n"
        f"   字段：{fields}"
    )


def classify(text: str, auto: AutoChoice, chooser: Chooser | None) -> Classification:
    """给一张单据选模板。text 是单据开头的文字。"""
    if not auto.candidates:
        return Classification(None, "还没有自己的模板，抽出了所有键值对", "none")
    scored = sorted(
        ((c, keyword_score(text, c.template)) for c in auto.candidates),
        key=lambda pair: -pair[1],
    )
    shortlist = tuple(scored[:SHORTLIST])
    if chooser is not None:
        answer = chooser(text, [describe(c.template) for c, _ in shortlist])
        if answer is not None and 0 <= answer[0] <= len(shortlist):
            index, reason = answer
            picked = shortlist[index - 1][0] if index > 0 else None
            reason = reason.strip() or ("模型认为都不合适" if picked is None else "模型选的")
            return Classification(picked, reason, "model", shortlist)
    best, score = shortlist[0]
    if score >= MIN_KEYWORD_SCORE:
        hits = round(min(score, 1.0) * len(_all_fields(best.template)))
        return Classification(
            best, f"按关键字选的：单据上对上了约 {hits} 个字段名", "keywords", shortlist
        )
    return Classification(None, "按关键字找不到合适的模板，抽出了所有键值对", "keywords", shortlist)
