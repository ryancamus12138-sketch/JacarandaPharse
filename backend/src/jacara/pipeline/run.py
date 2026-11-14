"""Pipeline: a file → one DocumentReport per page.

Read → OCR → page structure analysis → extraction → alignment → validation. The template
(templates/*.toml) decides which fields and tables are extracted and which rules are checked. The
CLI (`jacara extract`) and the background worker (`jacara worker`) share this code, so their
results are always identical. This module only computes; it neither prints nor touches the
database."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import cast

import numpy as np

from jacara.core.i18n import stored, tr
from jacara.pipeline.classify import AutoChoice, Chooser, classify, head_text
from jacara.pipeline.compute import compute
from jacara.pipeline.fieldcheck import Zoomer, check_fields, rule_hints
from jacara.pipeline.learn import Lesson, curate, note_for, pick
from jacara.pipeline.pages import stack_images, stack_pages, stacked_engines
from jacara.pipeline.ranges import range_settings
from jacara.pipeline.segment import Judge, segments
from jacara.pipeline.vision import MAX_IMAGES, VisionMode, to_png, vision_reason
from jacara.pipeline.zoom import Zoom
from jacara.plugins.aligners import BlockAligner
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import Extractor
from jacara.plugins.extractors.base import DISCOVER, ExtractedField
from jacara.plugins.ocr import OcrEngine, OcrPage
from jacara.plugins.ocr.loader import PdfRead, load_pages, page_images
from jacara.plugins.validators.base import ValidationResult
from jacara.structure.analyze import analyze_page
from jacara.structure.engines import NO_MODELS, StructureEngines
from jacara.structure.graph import PageStructure
from jacara.templates import DEFAULT_TEMPLATE, Template, load_template

TemplateChoice = str | Template | AutoChoice
"""Template to use: a built-in template name, a Template (a version of my template), DISCOVER (no
template), or AutoChoice (each document picks one of my templates automatically)."""

Progress = Callable[[str, int], None]
"""Progress callback: (current step, 0–100). The background worker writes it to the database for the
dashboard."""


@dataclass
class Pipeline:
    """Assembles the plugins of each step. Swapping a plugin only requires passing a different
    object."""

    ocr: OcrEngine
    extractor: Extractor
    aligner: BlockAligner = field(default_factory=BlockAligner)
    structure: StructureEngines = NO_MODELS
    """Layout and table models for page structure analysis. No models by default (rules only); the
    worker and the CLI pass default_engines()."""
    vision: VisionMode = "off"
    """When the model is shown the page image (JACARA_VISION). Never when the model server has no
    vision projector loaded."""
    pdf_read: PdfRead = "image"
    """How PDF text is read (JACARA_PDF_READ): image renders pages for OCR and uses the text layer
    only for cross-checking; text reads only the text layer."""
    field_check: bool = True
    """Check each template field against the PDF text layer, form fields and missed values."""
    zoom: bool = True
    """Zoomed re-recognition for conflicts and possibly missed values."""
    lessons: Callable[[str], Sequence[Lesson]] | None = None
    """Learning from corrections: template name → past manual corrections. Not used when absent."""
    learn_mode: str = "recent"
    """Example selection: recent (recently and frequently corrected) or curated."""

    def read(self, path: Path, pdf_read: PdfRead | None = None) -> list[OcrPage]:
        """Read the file: images through OCR; PDFs rendered for OCR by default, with the text layer
        used only for cross-checking."""
        return load_pages(path, self.ocr, pdf_read or self.pdf_read)

    def needs_images(self, vision: VisionMode = "off") -> bool:
        """Whether pages must be rendered: only when layout or table models are used, or the model
        may be shown the page image."""
        if self.structure.layout is not None or self.structure.table is not None:
            return True
        return vision != "off" and self.sees_images()

    def sees_images(self) -> bool:
        """Whether the model can process images."""
        check = getattr(self.extractor, "sees_images", None)
        return bool(check()) if callable(check) else False

    def extract_page(
        self,
        page: OcrPage,
        source: str,
        number: int,
        template: TemplateChoice = DEFAULT_TEMPLATE,
        image: np.ndarray | None = None,
        structure: PageStructure | None = None,
        images: Sequence[bytes] = (),
        vision: str | None = None,
        zoom: Zoomer | None = None,
    ) -> DocumentReport:
        """Run one page through structure analysis → extraction → alignment → validation.

                image is the page image (needed by layout and table models); structure is an
        already analyzed structure (stored structures are reused by trial runs); images are page
        images (PNG) for the model and vision is why they are shown; zoom is zoomed
        re-recognition, disabled when absent."""
        manual: frozenset[str] = frozenset()
        computed: frozenset[str] = frozenset()
        tpl: Template | None = None
        if structure is None:
            structure = analyze_page(page, image, self.structure)
        validator = None
        checks = ValidationResult((), (), ())  # No template, no rules.
        if template == DISCOVER:
            result = self.extractor.discover(page, structure)
            name, version = DISCOVER, 1
        else:
            if isinstance(template, AutoChoice):
                raise TypeError("AutoChoice templates go through extract_pages")
            tpl = template if isinstance(template, Template) else load_template(template)
            taught = self._teach(tpl, page)
            try:
                if images:
                    result = self.extractor.extract(
                        page, tpl.specs, tpl.table_specs, structure, images=images
                    )
                    result = replace(result, vision=vision or stored("看了原图"))
                else:
                    result = self.extractor.extract(page, tpl.specs, tpl.table_specs, structure)
            finally:
                if taught:
                    self.extractor.lessons = ("", 0)  # type: ignore[attr-defined]
                    self.extractor.lessons_mode = "recent"  # type: ignore[attr-defined]
            if tpl.manual_fields:
                # Manual-entry fields are not sent to the model; an empty value is stored and filled
                # during review.
                empty = tuple(ExtractedField(f.name, f.type, None) for f in tpl.manual_fields)
                result = replace(result, fields=result.fields + empty)
                manual = frozenset(f.name for f in tpl.manual_fields)
            name, version = tpl.name, tpl.version
            validator = tpl.validator()
        day_first = tpl.day_first if tpl is not None else None
        aligned = self.aligner.align(page, result, structure, day_first)
        if self.field_check:
            # Check each template field: text layer and form fields serve only as candidates; missed
            # values are looked for next to their labels.
            aligned = check_fields(aligned, page, tpl, zoom if self.zoom else None, day_first)
        if tpl is not None and tpl.formulas():
            # Computed fields use the cross-checked values; values rejected by rule count as absent.
            now = {a.field.name: None if a.status == "rejected" else a.field.value for a in aligned}
            done = compute(tpl, now)
            extra = tuple(ExtractedField(c.name, "number", c.value) for c in done)
            result = replace(result, fields=result.fields + extra)
            aligned = aligned + tuple(
                AlignedField(
                    field=f,
                    status="computed" if c.value is not None else "empty",
                    evidence=None,
                    needs_review=False,
                    note=c.note,
                )
                for f, c in zip(extra, done, strict=True)
            )
            computed = frozenset(c.name for c in done)
        if validator is not None:
            # Values rejected by cross-checking also count as absent during validation.
            rejected = {a.field.name for a in aligned if a.status == "rejected"}
            fields = [replace(f, value=None) if f.name in rejected else f for f in result.fields]
            checks = validator.validate(fields)
            aligned = rule_hints(aligned, fields, validator)
        return DocumentReport(
            source=source,
            extraction=result,
            aligned=aligned,
            checks=checks,
            ocr_engine=page.models.get("engine", page.engine),
            processed_at=datetime.now(),
            page=number,
            text_source=page.engine if page.engine == "pdf-text" else "ocr",
            template=name,
            template_version=version,
            ocr_page=page,
            manual=manual,
            computed=computed,
            structure=structure,
            ranges=range_settings(tpl),
        )

    def _teach(self, template: Template, page: OcrPage) -> bool:
        """Pass past manual corrections to the extractor. Returns True if passed; they must be
        cleared after extraction."""
        if self.lessons is None or not hasattr(self.extractor, "lessons"):
            return False
        found = self.lessons(template.name)
        if self.learn_mode == "curated":
            text = "\n".join(line.text for line in page.lines)
            chosen = curate(found, text, template)
        else:
            chosen = pick(found)
        if not chosen:
            return False
        self.extractor.lessons = (note_for(chosen), len(chosen))  # type: ignore[attr-defined]
        if self.learn_mode == "curated":
            self.extractor.lessons_mode = "curated"  # type: ignore[attr-defined]
        return True

    def extract_pages(
        self,
        pages: list[OcrPage],
        images: list[np.ndarray | None],
        source: str,
        first: int,
        template: TemplateChoice = DEFAULT_TEMPLATE,
        before_extract: Callable[[], None] | None = None,
        vision: VisionMode | None = None,
        written: Callable[[int, int], None] | None = None,
        history: Mapping[str, int] | None = None,
        zoom_for: Callable[[int, Sequence[float]], Zoomer] | None = None,
    ) -> DocumentReport:
        """Run one document (possibly several pages) through structure analysis → extraction →
        alignment → validation.

                Several pages are stacked into one tall page and processed together, so line-item
        tables spanning pages are joined and fields are found on any page. first is the first
        page of this document in the file; before_extract is called after layout analysis and
        before extraction (to report progress); vision decides whether the page image is shown
        this time (defaults to Pipeline.vision). written(written, expected) reports the
        characters written while the model streams; expected is estimated from history (template
        name → average past answer length), or from the field count and page line count without
        history. When the template is AutoChoice, a template is selected for this document first.
        zoom_for(first, page offsets) provides zoomed re-recognition for this document."""
        stacked = stack_pages(pages)
        image = stack_images(images, stacked)
        starts = stacked.starts if len(pages) > 1 else ()
        structure = analyze_page(
            stacked.page, image, stacked_engines(self.structure, stacked), page_starts=starts
        )
        if before_extract is not None:
            before_extract()
        classified = None
        if isinstance(template, AutoChoice):
            chooser = getattr(self.extractor, "choose", None)
            classified = classify(
                head_text(stacked.page),
                template,
                cast(Chooser, chooser) if callable(chooser) else None,
            )
            template = classified.picked.template if classified.picked else DISCOVER
        reason = vision_reason(pages, vision or self.vision) if template != DISCOVER else None
        listening = written is not None and hasattr(self.extractor, "on_text")
        if listening and written is not None:
            expected = expected_chars(template, stacked.page, history or {})
            self.extractor.on_text = lambda n: written(n, expected)  # type: ignore[attr-defined]
        shown = [img for img in images[:MAX_IMAGES] if img is not None]
        looks = reason is not None and bool(shown) and self.sees_images()
        try:
            report = self.extract_page(
                stacked.page,
                source,
                first,
                template,
                structure=structure,
                images=[to_png(img) for img in shown] if looks else (),
                vision=reason if looks else None,
                zoom=zoom_for(first, stacked.starts) if zoom_for is not None else None,
            )
        finally:
            if listening:
                self.extractor.on_text = None  # type: ignore[attr-defined]
        if classified is not None:
            picked = classified.picked
            report = replace(
                report,
                template_version_id=picked.version_id if picked else None,
                classified=classified.to_json(),
            )
        if len(pages) == 1:
            return report
        return replace(
            report,
            last_page=first + len(pages) - 1,
            pages=tuple(pages),
            page_starts=stacked.starts,
        )

    def run(
        self,
        path: Path,
        name: str | None = None,
        template: TemplateChoice = DEFAULT_TEMPLATE,
        progress: Progress | None = None,
        split: str = "auto",
        vision: VisionMode | None = None,
        history: Mapping[str, int] | None = None,
        pdf_read: PdfRead | None = None,
        pages: list[OcrPage] | None = None,
    ) -> list[DocumentReport]:
        """A whole file: one report per document. name is the display file name (defaults to the
        file name of path).

                split: number of documents in the file: auto, one, pages, or page ranges.
        pdf_read: how PDFs are read this time (defaults to Pipeline.pdf_read). When progress is
        set, every step reports once: OCR takes the first 10%, document splitting up to 15%, and
        extraction divides the rest equally among documents. pages: text already recognized (by
        the OCR stage of the pipeline); OCR is skipped when given."""
        report = progress or (lambda _stage, _percent: None)
        name = name or path.name
        if pages is None:
            report(stored("识别文字"), 5)
            pages = self.read(path, pdf_read)
        looks = vision or self.vision
        images = page_images(path) if self.needs_images(looks) else [None] * len(pages)
        judge = getattr(self.extractor, "continues", None)
        parts = segments(
            pages,
            split,
            cast(Judge, judge) if callable(judge) else None,
            lambda k: report(
                stored("判断第 {k} / {n} 页是不是新的一张单据", k=k, n=len(pages)),
                10 + 5 * k // len(pages),
            ),
        )
        reports: list[DocumentReport] = []
        for n, (a, b) in enumerate(parts, start=1):
            low = 20 + 75 * (n - 1) // len(parts)
            high = 20 + 75 * n // len(parts)
            where = stored("第 {a} 页", a=a) if a == b else stored("第 {a}–{b} 页", a=a, b=b)
            which = (
                stored("第 {n} / {count} 张单据（{where}）", n=n, count=len(parts), where=where)
                if len(parts) > 1
                else where
            )
            report(stored("分析{which}的版面", which=which), 15 + 80 * (n - 1) // len(parts))
            reports.append(
                self.extract_pages(
                    pages[a - 1 : b],
                    list(images[a - 1 : b]),
                    page_label(name, a, b, len(pages)),
                    a,
                    template,
                    before_extract=lambda which=which, n=n: report(
                        stored("选模板、抽取{which}", which=which)
                        if isinstance(template, AutoChoice)
                        else stored("抽取{which}", which=which),
                        20 + 75 * (n - 1) // len(parts),
                    ),
                    vision=looks,
                    written=(writing(report, which, low, high) if progress is not None else None),
                    history=history,
                    zoom_for=(
                        (lambda first, starts: Zoom(path, self.ocr, first, starts))
                        if self.ocr is not None
                        else None
                    ),
                )
            )
        return reports


WRITE_SHARE = 0.95
"""When the model writes more than expected, progress stops at 95% of this segment until the answer
completes; it never goes backwards or beyond."""


def expected_chars(template: TemplateChoice, page: OcrPage, history: Mapping[str, int]) -> int:
    """Expected length of the model answer: the average past answer length for the template;
    otherwise a rough estimate of about 60 characters per field and 45 per table cell, with rows
    estimated as half the lines on the page."""
    name = (
        DISCOVER
        if template == DISCOVER
        else (template.name if isinstance(template, Template) else str(template))
    )
    if history.get(name):
        return max(200, int(history[name]))
    from jacara.plugins.ocr.layout import group_rows

    rows = len(group_rows(page))
    if template == DISCOVER or isinstance(template, AutoChoice):
        return max(800, rows * 90)
    tpl = template if isinstance(template, Template) else load_template(str(template))
    columns = sum(len(t.columns) for t in tpl.tables)
    return max(400, 60 * len(tpl.fields) + 45 * columns * max(1, rows // 2))


def writing(report: Progress, which: str, low: int, high: int) -> Callable[[int, int], None]:
    """Characters written by the model → progress: extraction of this document spans low–high and
    advances by written / expected; reported at most once per second with the estimated seconds
    remaining."""
    started = time.monotonic()
    last = [0.0]

    def tell(written: int, expected: int) -> None:
        now = time.monotonic()
        if now - last[0] < 1.0:
            return
        last[0] = now
        share = min(WRITE_SHARE, written / expected) if expected else WRITE_SHARE
        speed = written / max(now - started, 0.1)
        left = max(0, round((expected - written) / speed)) if speed and written < expected else 0
        hint = stored("，大约还要 {left} 秒", left=left) if left else stored("，快写完了")
        report(
            stored("抽取{which}：模型已写 {share:.0%}{hint}", which=which, share=share, hint=hint),
            low + round((high - low) * share),
        )

    return tell


def page_label(name: str, first: int, last: int, total: int) -> str:
    """Multi-page files are shown as "invoice.pdf page 2" or "invoice.pdf pages 2–3"; single-page
    files by file name."""
    if total <= 1:
        return name
    return (
        tr("{name} 第 {first} 页", name=name, first=first)
        if first == last
        else tr("{name} 第 {first}–{last} 页", name=name, first=first, last=last)
    )
