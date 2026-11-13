"""流水线：一个文件 → 每一页的 DocumentReport。

读取 → OCR → 分析页面结构（ADR 0034）→ 抽取 → 对齐 → 校验。
抽哪些字段和表格、查哪些规则，由模板决定（templates/*.toml）。
命令行（jacara extract）和后台任务（jacara worker）用的是同一份代码，所以两边的结果一定一样。
这里只计算、不打印、不碰数据库。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import cast

import numpy as np

from jacara.pipeline.classify import AutoChoice, Chooser, classify, head_text
from jacara.pipeline.compute import compute
from jacara.pipeline.fieldcheck import Zoomer, check_fields, rule_hints
from jacara.pipeline.learn import Lesson, note_for, pick
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
"""用哪个模板：内置模板的名字、一个 Template（我的模板的某个版本）、DISCOVER（不用模板），
或者 AutoChoice（每张单据自动从我的模板里挑，ADR 0038）。"""

Progress = Callable[[str, int], None]
"""报告进度：（现在在做什么，0～100）。后台任务用它把进度写进数据库，工作台显示。"""


@dataclass
class Pipeline:
    """把各步骤的插件组装在一起。换插件只要传不同的对象进来。"""

    ocr: OcrEngine
    extractor: Extractor
    aligner: BlockAligner = field(default_factory=BlockAligner)
    structure: StructureEngines = NO_MODELS
    """分析页面结构用的版面模型、表格模型。默认不用模型（只按规则）；
    后台和命令行传 default_engines()。"""
    vision: VisionMode = "off"
    """什么时候让模型看原图（ADR 0038，JACARA_VISION）。模型服务没加载视觉模型时不看。"""
    pdf_read: PdfRead = "image"
    """PDF 怎么读文字（ADR 0042，JACARA_PDF_READ）：image 画成图做 OCR，文字层只核对；
    text 只读文字层。"""
    field_check: bool = True
    """按模板字段逐一核对：PDF 文字层、表单字段、漏读的值（ADR 0042）。"""
    zoom: bool = True
    """有冲突、可能漏读时局部放大再识别（ADR 0042）。"""
    lessons: Callable[[str], Sequence[Lesson]] | None = None
    """从人改过的地方学（ADR 0045）：模板名 → 以前人工改过的例子。不给就不用。"""

    def read(self, path: Path, pdf_read: PdfRead | None = None) -> list[OcrPage]:
        """读文件：图片走 OCR；PDF 默认画成图走 OCR，文字层只核对（ADR 0042）。"""
        return load_pages(path, self.ocr, pdf_read or self.pdf_read)

    def needs_images(self, vision: VisionMode = "off") -> bool:
        """要不要把每页画成图：用版面模型、表格模型，或者可能让模型看原图时才要。"""
        if self.structure.layout is not None or self.structure.table is not None:
            return True
        return vision != "off" and self.sees_images()

    def sees_images(self) -> bool:
        """模型能不能看图（ADR 0038）。"""
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
        """一页走完 分析结构 → 抽取 → 对齐 → 校验。

        image 是这一页的图（用版面、表格模型时要）；
        structure 是已经分析好的结构（试跑时用存下来的）；
        images 是给模型看的原图（PNG），vision 是为什么看（ADR 0038）；
        zoom 是局部放大再识别（ADR 0042），不给就不放大。
        """
        manual: frozenset[str] = frozenset()
        computed: frozenset[str] = frozenset()
        tpl: Template | None = None
        if structure is None:
            structure = analyze_page(page, image, self.structure)
        validator = None
        checks = ValidationResult((), (), ())  # 没有模板，就没有规则
        if template == DISCOVER:
            result = self.extractor.discover(page, structure)
            name, version = DISCOVER, 1
        else:
            if isinstance(template, AutoChoice):
                raise TypeError("自动判断要用 extract_pages")
            tpl = template if isinstance(template, Template) else load_template(template)
            taught = self._teach(tpl.name)
            try:
                if images:
                    result = self.extractor.extract(
                        page, tpl.specs, tpl.table_specs, structure, images=images
                    )
                    result = replace(result, vision=vision or "看了原图")
                else:
                    result = self.extractor.extract(page, tpl.specs, tpl.table_specs, structure)
            finally:
                if taught:
                    self.extractor.lessons = ("", 0)  # type: ignore[attr-defined]
            if tpl.manual_fields:
                # 人工填的字段不交给模型，先放一个空值，审核时填（ADR 0031）
                empty = tuple(ExtractedField(f.name, f.type, None) for f in tpl.manual_fields)
                result = replace(result, fields=result.fields + empty)
                manual = frozenset(f.name for f in tpl.manual_fields)
            name, version = tpl.name, tpl.version
            validator = tpl.validator()
        day_first = tpl.day_first if tpl is not None else None
        aligned = self.aligner.align(page, result, structure, day_first)
        if self.field_check:
            # 按模板字段逐一核对（ADR 0042）：文字层、表单字段只当候选，漏读的去标签旁边找
            aligned = check_fields(aligned, page, tpl, zoom if self.zoom else None, day_first)
        if tpl is not None and tpl.formulas():
            # 计算字段（ADR 0035）：用核对过的值算，按规则没有填的（rejected）当作没有
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
            # 核对不上、按规则没有填的值（rejected），校验时也当作没有
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

    def _teach(self, template: str) -> bool:
        """把以前人工改过的例子交给抽取器（ADR 0045）。交了返回 True，抽完要清掉。"""
        if self.lessons is None or not hasattr(self.extractor, "lessons"):
            return False
        chosen = pick(self.lessons(template))
        if not chosen:
            return False
        self.extractor.lessons = (note_for(chosen), len(chosen))  # type: ignore[attr-defined]
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
        """一张单据（可能有好几页）走完 分析结构 → 抽取 → 对齐 → 校验（ADR 0036）。

        好几页的拼成上下相连的一张长页一起处理：跨页的明细表接在一起，字段在哪一页都能找到。
        first 是这张单据在文件里从第几页开始；
        before_extract 在分析完版面、开始抽取前调用（报进度）；
        vision 是这一次看不看原图（不写用 Pipeline.vision，ADR 0038）。
        written(已写, 预计) 在模型边写边传时报告写了多少字；预计按 history（模板名 → 以前回答的
        平均长度）估，没有历史就按字段数和页面的行数估。
        模板是 AutoChoice 时先给这张单据选模板（ADR 0038）。
        zoom_for(first, 每页的起点) 给这张单据做局部放大再识别（ADR 0042）。
        """
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
    ) -> list[DocumentReport]:
        """整个文件：每张单据一份报告。name 是显示用的文件名（默认取 path 的文件名）。

        split：文件里有几张单据（ADR 0036）：auto 自动判断、one、pages 或页码范围。
        pdf_read：这一次 PDF 怎么读（ADR 0042），不写用 Pipeline.pdf_read。
        progress 不为空时，每开始一步就报告一次：识别文字占前 10%，分单据到 15%，
        抽取按单据平分剩下的。
        """
        report = progress or (lambda _stage, _percent: None)
        name = name or path.name
        report("识别文字", 5)
        pages = self.read(path, pdf_read)
        looks = vision or self.vision
        images = page_images(path) if self.needs_images(looks) else [None] * len(pages)
        judge = getattr(self.extractor, "continues", None)
        parts = segments(
            pages,
            split,
            cast(Judge, judge) if callable(judge) else None,
            lambda k: report(
                f"判断第 {k} / {len(pages)} 页是不是新的一张单据", 10 + 5 * k // len(pages)
            ),
        )
        reports: list[DocumentReport] = []
        for n, (a, b) in enumerate(parts, start=1):
            low = 20 + 75 * (n - 1) // len(parts)
            high = 20 + 75 * n // len(parts)
            where = f"第 {a} 页" if a == b else f"第 {a}–{b} 页"
            which = f"第 {n} / {len(parts)} 张单据（{where}）" if len(parts) > 1 else where
            report(f"分析{which}的版面", 15 + 80 * (n - 1) // len(parts))
            reports.append(
                self.extract_pages(
                    pages[a - 1 : b],
                    list(images[a - 1 : b]),
                    page_label(name, a, b, len(pages)),
                    a,
                    template,
                    before_extract=lambda which=which, n=n: report(
                        f"选模板、抽取{which}"
                        if isinstance(template, AutoChoice)
                        else f"抽取{which}",
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
"""模型写的字超过预计时，进度停在这一段的 95%，等写完再往后走，不倒退、不超过。"""


def expected_chars(template: TemplateChoice, page: OcrPage, history: Mapping[str, int]) -> int:
    """模型这次大约要写多少字：同一个模板以前回答的平均长度；没有就粗估：
    每个字段约 60 字，表格每一格约 45 字、行数按页面上的行数的一半估。"""
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
    """模型写了多少字 → 进度：这张单据的抽取占 low～high，按 已写 / 预计 往前走；
    每秒最多报一次，说还要大约几秒。"""
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
        hint = f"，大约还要 {left} 秒" if left else "，快写完了"
        report(f"抽取{which}：模型已写 {share:.0%}{hint}", low + round((high - low) * share))

    return tell


def page_label(name: str, first: int, last: int, total: int) -> str:
    """多页文件显示成"发票.pdf 第 2 页""发票.pdf 第 2–3 页"，单页就是文件名。"""
    if total <= 1:
        return name
    return f"{name} 第 {first} 页" if first == last else f"{name} 第 {first}–{last} 页"
