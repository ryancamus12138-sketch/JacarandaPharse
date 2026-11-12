"""流水线：一个文件 → 每一页的 DocumentReport。

读取 → OCR → 抽取 → 对齐 → 校验。抽哪些字段和表格、查哪些规则，由模板决定（templates/*.toml）。
命令行（jacara extract）和后台任务（jacara worker）用的是同一份代码，所以两边的结果一定一样。
这里只计算、不打印、不碰数据库。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import Extractor
from jacara.plugins.extractors.base import DISCOVER, ExtractedField
from jacara.plugins.ocr import OcrEngine, OcrPage
from jacara.plugins.ocr.loader import load_pages
from jacara.plugins.validators.base import ValidationResult
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
    aligner: TextMatchAligner = field(default_factory=TextMatchAligner)

    def read(self, path: Path) -> list[OcrPage]:
        """读文件：图片走 OCR，PDF 有文字层直接读。"""
        return load_pages(path, self.ocr)

    def extract_page(
        self, page: OcrPage, source: str, number: int, template: TemplateChoice = DEFAULT_TEMPLATE
    ) -> DocumentReport:
        """一页走完 抽取 → 对齐 → 校验。"""
        manual: frozenset[str] = frozenset()
        if template == DISCOVER:
            result = self.extractor.discover(page)
            name, version = DISCOVER, 1
            checks = ValidationResult((), (), ())  # 没有模板，就没有规则
        else:
            tpl = template if isinstance(template, Template) else load_template(template)
            result = self.extractor.extract(page, tpl.specs, tpl.table_specs)
            if tpl.manual_fields:
                # 人工填的字段不交给模型，先放一个空值，审核时填（ADR 0031）
                empty = tuple(ExtractedField(f.name, f.type, None) for f in tpl.manual_fields)
                result = replace(result, fields=result.fields + empty)
                manual = frozenset(f.name for f in tpl.manual_fields)
            name, version = tpl.name, tpl.version
            checks = tpl.validator().validate(result.fields)
        return DocumentReport(
            source=source,
            extraction=result,
            aligned=self.aligner.align(page, result),
            checks=checks,
            ocr_engine=page.models.get("engine", page.engine),
            processed_at=datetime.now(),
            page=number,
            text_source=page.engine if page.engine == "pdf-text" else "ocr",
            template=name,
            template_version=version,
            ocr_page=page,
            manual=manual,
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
        reports: list[DocumentReport] = []
        for n, page in enumerate(pages, start=1):
            report(f"抽取第 {n} / {len(pages)} 页", 20 + 75 * (n - 1) // len(pages))
            reports.append(self.extract_page(page, page_label(name, n, len(pages)), n, template))
        return reports


def page_label(name: str, number: int, total: int) -> str:
    """多页文件显示成"发票.pdf 第 2 页"，单页就是文件名。"""
    return f"{name} 第 {number} 页" if total > 1 else name
