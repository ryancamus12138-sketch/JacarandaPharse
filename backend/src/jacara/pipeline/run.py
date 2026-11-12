"""流水线：一个文件 → 每一页的 DocumentReport。

读取 → OCR → 分析页面结构（ADR 0034）→ 抽取 → 对齐 → 校验。
抽哪些字段和表格、查哪些规则，由模板决定（templates/*.toml）。
命令行（jacara extract）和后台任务（jacara worker）用的是同一份代码，所以两边的结果一定一样。
这里只计算、不打印、不碰数据库。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from jacara.pipeline.compute import compute
from jacara.plugins.aligners import BlockAligner
from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import Extractor
from jacara.plugins.extractors.base import DISCOVER, ExtractedField
from jacara.plugins.ocr import OcrEngine, OcrPage
from jacara.plugins.ocr.loader import load_pages, page_images
from jacara.plugins.validators.base import ValidationResult
from jacara.structure.analyze import analyze_page
from jacara.structure.engines import NO_MODELS, StructureEngines
from jacara.structure.graph import PageStructure
from jacara.templates import DEFAULT_TEMPLATE, Template, load_template

TemplateChoice = str | Template
"""用哪个模板：内置模板的名字、一个 Template（我的模板的某个版本），或者 DISCOVER（不用模板）。"""

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

    def read(self, path: Path) -> list[OcrPage]:
        """读文件：图片走 OCR，PDF 有文字层直接读。"""
        return load_pages(path, self.ocr)

    def needs_images(self) -> bool:
        """要不要把每页画成图：用版面模型或表格模型时才要。"""
        return self.structure.layout is not None or self.structure.table is not None

    def extract_page(
        self,
        page: OcrPage,
        source: str,
        number: int,
        template: TemplateChoice = DEFAULT_TEMPLATE,
        image: np.ndarray | None = None,
        structure: PageStructure | None = None,
    ) -> DocumentReport:
        """一页走完 分析结构 → 抽取 → 对齐 → 校验。

        image 是这一页的图（用版面、表格模型时要）；
        structure 是已经分析好的结构（试跑时用存下来的）。
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
            tpl = template if isinstance(template, Template) else load_template(template)
            result = self.extractor.extract(page, tpl.specs, tpl.table_specs, structure)
            if tpl.manual_fields:
                # 人工填的字段不交给模型，先放一个空值，审核时填（ADR 0031）
                empty = tuple(ExtractedField(f.name, f.type, None) for f in tpl.manual_fields)
                result = replace(result, fields=result.fields + empty)
                manual = frozenset(f.name for f in tpl.manual_fields)
            name, version = tpl.name, tpl.version
            validator = tpl.validator()
        aligned = self.aligner.align(page, result, structure)
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
        )

    def run(
        self,
        path: Path,
        name: str | None = None,
        template: TemplateChoice = DEFAULT_TEMPLATE,
        progress: Progress | None = None,
    ) -> list[DocumentReport]:
        """整个文件：每一页一份报告。name 是显示用的文件名（默认取 path 的文件名）。

        progress 不为空时，每开始一步就报告一次：识别文字占前 20%，抽取按页数平分剩下的。
        """
        report = progress or (lambda _stage, _percent: None)
        name = name or path.name
        report("识别文字", 5)
        pages = self.read(path)
        images = page_images(path) if self.needs_images() else [None] * len(pages)
        reports: list[DocumentReport] = []
        for n, (page, image) in enumerate(zip(pages, images, strict=False), start=1):
            report(f"分析第 {n} / {len(pages)} 页的版面", 15 + 75 * (n - 1) // len(pages))
            structure = analyze_page(page, image, self.structure)
            report(f"抽取第 {n} / {len(pages)} 页", 20 + 75 * (n - 1) // len(pages))
            reports.append(
                self.extract_page(
                    page, page_label(name, n, len(pages)), n, template, structure=structure
                )
            )
        return reports


def page_label(name: str, number: int, total: int) -> str:
    """多页文件显示成"发票.pdf 第 2 页"，单页就是文件名。"""
    return f"{name} 第 {number} 页" if total > 1 else name
