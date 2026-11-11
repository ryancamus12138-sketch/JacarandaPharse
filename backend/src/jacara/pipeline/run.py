"""流水线：一个文件 → 每一页的 DocumentReport。

读取 → OCR → 抽取 → 对齐 → 校验。抽哪些字段和表格、查哪些规则，由模板决定（templates/*.toml）。
命令行（jacara extract）和后台任务（jacara worker）用的是同一份代码，所以两边的结果一定一样。
这里只计算、不打印、不碰数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import Extractor
from jacara.plugins.ocr import OcrEngine, OcrPage
from jacara.plugins.ocr.loader import load_pages
from jacara.plugins.validators import RuleValidator
from jacara.templates import DEFAULT_TEMPLATE, load_template


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
        self, page: OcrPage, source: str, number: int, template: str = DEFAULT_TEMPLATE
    ) -> DocumentReport:
        """一页走完 抽取 → 对齐 → 校验。"""
        tpl = load_template(template)
        result = self.extractor.extract(page, tpl.specs, tpl.table_specs)
        return DocumentReport(
            source=source,
            extraction=result,
            aligned=self.aligner.align(page, result),
            checks=RuleValidator(tpl.rules).validate(result.fields),
            ocr_engine=page.models.get("engine", page.engine),
            processed_at=datetime.now(),
            page=number,
            text_source=page.engine if page.engine == "pdf-text" else "ocr",
            template=tpl.name,
            template_version=tpl.version,
            ocr_page=page,
        )

    def run(
        self, path: Path, name: str | None = None, template: str = DEFAULT_TEMPLATE
    ) -> list[DocumentReport]:
        """整个文件：每一页一份报告。name 是显示用的文件名（默认取 path 的文件名）。"""
        name = name or path.name
        pages = self.read(path)
        return [
            self.extract_page(page, page_label(name, n, len(pages)), n, template)
            for n, page in enumerate(pages, start=1)
        ]


def page_label(name: str, number: int, total: int) -> str:
    """多页文件显示成"发票.pdf 第 2 页"，单页就是文件名。"""
    return f"{name} 第 {number} 页" if total > 1 else name
