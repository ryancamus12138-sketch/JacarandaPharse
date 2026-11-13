"""统计和数据导出（ADR 0044）。

只看"完成审核"的单据、现在用的那一轮：人看过、确认过的值才能当标准答案。
- 修改统计：每个字段人改了多少、改的是哪种错误、错了却没标黄的有几个，按模板、按字段汇总。
- 数据导出：审核过的单据打包成 zip：原文件、成对测评清单（jacara eval pairs 直接能用）、
  训练数据（每张单据的 OCR 文字、模型的回答、人改后的值、改了哪些）。敏感字段默认不导出。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage
from jacara.modules.audit.service import record
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.fields.models import ExtractedValue
from jacara.modules.insights.kinds import KIND_NAMES, error_kind
from jacara.modules.insights.schemas import (
    Correction,
    CorrectionStats,
    FieldStat,
    KindCount,
    TemplateStat,
)
from jacara.pipeline.current import current_values, label_for, labels_of, template_of
from jacara.plugins.extractors.base import DISCOVER, split_cell_name
from jacara.templates import STARTERS, Template

RECENT = 30
"""统计页列出最近多少次修改。"""

HIDDEN = "（敏感字段，不显示）"


@dataclass
class _Row:
    value: ExtractedValue
    extraction: Extraction
    document: Document
    template_key: str
    template_title: str
    field: str
    label: str
    sensitive: bool


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").strip() == (b or "").strip()


def _generic(name: str) -> str:
    cell = split_cell_name(name)
    return f"{cell[0]}.{cell[2]}" if cell else name


def _title(extraction: Extraction, template: Template | None) -> str:
    if extraction.template == DISCOVER:
        return "抽取所有键值对"
    return template.title if template else extraction.template


def _sensitive_names(template: Template | None) -> set[str]:
    if template is None:
        return set()
    names = {f.name for f in template.fields if f.sensitive}
    for table in template.tables:
        names |= {f"{table.name}.{c.name}" for c in table.columns if getattr(c, "sensitive", False)}
    return names


class InsightsService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.owner = owner
        self.storage = storage

    def _documents(self, template: str | None, days: int | None) -> list[Document]:
        query = (
            select(Document)
            .where(Document.team_id == self.owner.team_id, Document.status == "reviewed")
            .options(
                selectinload(Document.extractions)
                .selectinload(Extraction.values)
                .selectinload(ExtractedValue.revisions)
            )
            .order_by(Document.reviewed_at.desc())
        )
        if days:
            query = query.where(Document.reviewed_at >= datetime.now(UTC) - timedelta(days=days))
        documents = list(self.session.scalars(query))
        if template:
            documents = [d for d in documents if any(_key(e) == template for e in d.extractions)]
        return documents

    def _rows(self, documents: list[Document]) -> list[_Row]:
        rows: list[_Row] = []
        for document in documents:
            for extraction in document.extractions:
                template = template_of(extraction)
                labels = labels_of(extraction)
                sensitive = _sensitive_names(template)
                title = _title(extraction, template)
                for value in extraction.values:
                    if value.origin != "model" or value.deleted:
                        continue  # 人工加的行、按公式算的不算模型的错
                    field = _generic(value.name)
                    rows.append(
                        _Row(
                            value,
                            extraction,
                            document,
                            _key(extraction),
                            title,
                            field,
                            label_for(labels, value.name) or value.label or value.name,
                            field in sensitive,
                        )
                    )
        return rows

    def corrections(self, template: str | None = None, days: int | None = None) -> CorrectionStats:
        require(self.owner, "document:read")
        documents = self._documents(template, days)
        rows = [r for r in self._rows(documents) if template is None or r.template_key == template]
        edited = [
            r for r in rows if r.value.revisions and not _same(r.value.value, r.value.current_value)
        ]
        confirmed = [r for r in rows if r.value.revisions and r not in edited]
        silent = [r for r in edited if not r.value.needs_review]
        kinds = {
            id(r): error_kind(r.value.type, r.value.value, r.value.current_value) for r in edited
        }

        per_template: dict[str, list[_Row]] = defaultdict(list)
        per_field: dict[tuple[str, str], list[_Row]] = defaultdict(list)
        for r in rows:
            per_template[r.template_key].append(r)
            per_field[(r.template_key, r.field)].append(r)
        edited_ids = {id(r) for r in edited}

        def kind_counts(group: list[_Row]) -> list[KindCount]:
            counter = Counter(kinds[id(r)] for r in group if id(r) in edited_ids)
            return [
                KindCount(kind=k, name=KIND_NAMES[k], count=n) for k, n in counter.most_common()
            ]

        templates = [
            TemplateStat(
                template=key,
                template_title=group[0].template_title,
                documents=len({r.document.id for r in group}),
                fields=len(group),
                edited=sum(id(r) in edited_ids for r in group),
                silent=sum(id(r) in edited_ids and not r.value.needs_review for r in group),
                rate=_rate(sum(id(r) in edited_ids for r in group), len(group)),
            )
            for key, group in per_template.items()
        ]
        templates.sort(key=lambda t: (-t.edited, t.template_title))
        fields = [
            FieldStat(
                template=key,
                template_title=group[0].template_title,
                field=field,
                label=re.sub(r"第 \d+ 行 · ", "", group[0].label),
                total=len(group),
                edited=sum(id(r) in edited_ids for r in group),
                silent=sum(id(r) in edited_ids and not r.value.needs_review for r in group),
                rate=_rate(sum(id(r) in edited_ids for r in group), len(group)),
                kinds=kind_counts(group),
            )
            for (key, field), group in per_field.items()
        ]
        fields.sort(key=lambda f: (-f.edited, -f.rate, f.label))
        recent = sorted(edited, key=lambda r: r.value.revisions[-1].created_at, reverse=True)
        return CorrectionStats(
            documents=len(documents),
            fields=len(rows),
            edited=len(edited),
            confirmed=len(confirmed),
            silent=len(silent),
            rate=_rate(len(edited), len(rows)),
            kinds=kind_counts(rows),
            templates=templates,
            by_field=[f for f in fields if f.total > 0],
            recent=[
                Correction(
                    document_id=r.document.id,
                    filename=r.document.filename,
                    template_title=r.template_title,
                    label=r.label,
                    before=HIDDEN if r.sensitive else r.value.value,
                    after=HIDDEN if r.sensitive else r.value.current_value,
                    kind=kinds[id(r)],
                    kind_name=KIND_NAMES[kinds[id(r)]],
                    flagged=r.value.needs_review,
                    reason=r.value.revisions[-1].reason,
                    at=r.value.revisions[-1].created_at,
                )
                for r in recent[:RECENT]
            ],
        )

    # ---------- 数据导出 ----------

    def dataset(
        self, template: str | None = None, sensitive: bool = False, files: bool = True
    ) -> tuple[str, bytes]:
        """审核过的单据 → zip（文件名, 内容）。

        要能导出文档（document:export），带原文件还要能下载（document:download）。"""
        require(self.owner, "document:export")
        if files:
            require(self.owner, "document:download")
        documents = self._documents(template, None)
        out = io.BytesIO()
        manifest: list[str] = [
            "# 成对测评清单（ADR 0042）：jacara eval pairs --manifest 这个文件",
            '# 标准答案是审核完成后的当前值；照片和 PDF 成对的，自己加上 photo = "..."',
            "",
        ]
        templates: dict[str, dict[str, Any]] = {}
        count = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            train = io.StringIO()
            for document in documents:
                extractions = [
                    e for e in document.extractions if template is None or _key(e) == template
                ]
                if not extractions:
                    continue
                stored = None
                if files and document.storage_key and self.storage is not None:
                    stored = f"files/{document.id}-{_safe(document.filename)}"
                    archive.writestr(stored, self.storage.get(document.storage_key))
                for extraction in extractions:
                    tpl = template_of(extraction)
                    hidden = set() if sensitive else _sensitive_names(tpl)
                    truth: dict[str, str] = {}
                    answer: dict[str, str | None] = {}
                    changes: list[dict[str, Any]] = []
                    for value, name in current_values(extraction):
                        if _generic(name) in hidden:
                            continue
                        if value.current_value is not None:
                            truth[name] = value.current_value
                        if value.origin == "model":
                            answer[name] = value.value
                            if value.revisions and not _same(value.value, value.current_value):
                                kind = error_kind(value.type, value.value, value.current_value)
                                changes.append(
                                    {
                                        "field": name,
                                        "before": value.value,
                                        "after": value.current_value,
                                        "kind": kind,
                                        "flagged": value.needs_review,
                                    }
                                )
                    key = _key(extraction)
                    entry = {
                        "document": str(document.id),
                        "file": stored,
                        "pages": [extraction.page, extraction.last_page or extraction.page],
                        "template": key,
                        "template_title": _title(extraction, tpl),
                        "ocr_text": _ocr_text(extraction.ocr),
                        "model": extraction.model,
                        "prompt_version": extraction.prompt_version,
                        "model_answer": answer,
                        "truth": truth,
                        "corrections": changes,
                    }
                    train.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    count += 1
                    if stored and extraction.page == 1 and not extraction.last_page:
                        manifest += _manifest_entry(document, extraction, stored, key, truth)
                        if key not in STARTERS and extraction.template_def is not None and tpl:
                            templates[key] = {
                                "name": key,
                                "title": tpl.title,
                                "version": tpl.version,
                                "definition": extraction.template_def.definition,
                            }
            archive.writestr("train.jsonl", train.getvalue())
            archive.writestr("pairs.toml", "\n".join(manifest) + "\n")
            for key, data in templates.items():
                archive.writestr(
                    f"templates/{key}.json", json.dumps(data, ensure_ascii=False, indent=2)
                )
            archive.writestr(
                "README.txt", README.format(count=count, sensitive="含" if sensitive else "不含")
            )
        record(
            self.session,
            self.owner,
            "document:dataset",
            "document",
            None,
            documents=len(documents),
            template=template,
            sensitive=sensitive,
            files=files,
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return f"审核数据-{stamp}.zip", out.getvalue()


README = """审核过的单据导出（JacarandaParse，ADR 0044）

共 {count} 张单据，{sensitive}敏感字段。

- files/：原文件。
- train.jsonl：一行一张单据：OCR 文字（ocr_text）、模型的回答（model_answer）、
  人审核后的值（truth）、人改了哪些（corrections，含错误类型）、用的模型和提示词版本。
  可以拿来评测，也可以整理成微调数据。
- pairs.toml：成对测评清单，放进 data/eval/ 后运行
  uv run jacara eval pairs --manifest data/eval/这个文件夹/pairs.toml
  我的模板的定义在 templates/ 里。只占一页的单据才列进去。

这些是你自己的业务数据，请按你们的数据管理规定保存，不要放进代码仓库。
"""


def _key(extraction: Extraction) -> str:
    return extraction.template


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _safe(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", name)[-80:]


def _ocr_text(ocr: dict[str, Any] | None) -> str:
    if not ocr:
        return ""
    pages = ocr.get("pages") or [ocr]
    return "\n\n".join(
        "\n".join(line.get("text", "") for line in p.get("lines", [])) for p in pages
    )


def _toml(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _manifest_entry(
    document: Document, extraction: Extraction, stored: str, key: str, truth: dict[str, str]
) -> list[str]:
    is_pdf = document.content_type == "application/pdf"
    lines = ["[[doc]]", f"id = {_toml(str(document.id)[:8] + '-' + document.filename)}"]
    if key in STARTERS or extraction.template_def is None:
        lines.append(f"template = {_toml(key)}")
    else:
        lines.append(f"template_file = {_toml(f'templates/{key}.json')}")
    lines.append(f"{'pdf' if is_pdf else 'photo'} = {_toml(stored)}")
    lines.append("[doc.truth]")
    lines += [f"{_toml(k)} = {_toml(v)}" for k, v in truth.items()]
    lines.append("")
    return lines
