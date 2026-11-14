"""Automatic template selection for documents.

With "automatic" selected at upload, each document picks the best match from my templates.
1. Score each template by keywords: how many of its field names, aliases and its name appear in
   the document text (fast, no model);
2. Hand the top candidates to the model, which reads the beginning of the document and the
   template descriptions and picks one, or none;
3. None fits: use no template and extract all key-value pairs (which can seed a new template).
Without a model to ask (CLI tests, or the model cannot decide), pick by keyword score; a score
that is too low means none fits."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import group_rows
from jacara.templates import Template, TemplateField

SHORTLIST = 5
"""Maximum number of templates offered to the model. More would lengthen the prompt and make wrong
picks likelier."""

HEAD_LINES = 60
"""Number of lines from the beginning of the document shown to the model. Type, header and country
usually appear early."""

MIN_KEYWORD_SCORE = 0.3
"""Minimum keyword score (share of matched fields) for a template to fit when there is no model."""

Chooser = Callable[[str, Sequence[str]], "tuple[int, str] | None"]
"""Ask the model: (document text, list of template descriptions) → (chosen index, 1-based, 0 meaning
none fits; reason). Returns None when the model cannot answer."""


@dataclass(frozen=True)
class Candidate:
    """A selectable template: the latest version of one of my templates."""

    template: Template
    template_id: uuid.UUID
    version_id: uuid.UUID


@dataclass(frozen=True)
class AutoChoice:
    """ "Automatic" selected at upload: candidate templates passed to the pipeline."""

    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Classification:
    picked: Candidate | None
    """Selected template; None if none fits (all key-value pairs are extracted instead)."""
    reason: str
    by: str
    """model: chosen by the model; keywords: chosen by keyword score; none: no templates
    available."""
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
    """Lines from the beginning of the document (visual lines)."""
    return "\n".join(r.text for r in group_rows(page)[:lines])


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _all_fields(template: Template) -> list[TemplateField]:
    fields = list(template.fields)
    for table in template.tables:
        fields += table.columns
    return fields


def keyword_score(text: str, template: Template) -> float:
    """Share of matched fields: a field matches when its display name or any alias appears in
    the document text. A template name appearing on the document ("增值税发票", "Tax Invoice")
    adds 0.5."""
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
    """Template description for the model: name, description, domains, country, fields to
    extract."""
    countries = "、".join(template.countries) or "通用（不分国家）"
    domains = "、".join(template.domains) or "（没写）"
    fields = "、".join(f.label for f in _all_fields(template)[:30])
    description = template.description.strip().replace("\n", " ")[:200]
    return (
        f"{template.title}：{description}\n   领域：{domains}；国家和地区：{countries}\n"
        f"   字段：{fields}"
    )


def classify(text: str, auto: AutoChoice, chooser: Chooser | None) -> Classification:
    """Select a template for one document. text is the beginning of the document text."""
    if not auto.candidates:
        return Classification(None, stored("还没有自己的模板，抽出了所有键值对"), "none")
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
            reason = reason.strip() or (
                stored("模型认为都不合适") if picked is None else stored("模型选的")
            )
            return Classification(picked, reason, "model", shortlist)
    best, score = shortlist[0]
    if score >= MIN_KEYWORD_SCORE:
        hits = round(min(score, 1.0) * len(_all_fields(best.template)))
        return Classification(
            best,
            stored("按关键字选的：单据上对上了约 {hits} 个字段名", hits=hits),
            "keywords",
            shortlist,
        )
    return Classification(
        None, stored("按关键字找不到合适的模板，抽出了所有键值对"), "keywords", shortlist
    )
