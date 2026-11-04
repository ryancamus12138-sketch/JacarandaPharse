"""命令行入口：`jacara`。

uv run jacara --version
uv run jacara ocr 图片或PDF                      识别文字（PDF 有文字层时直接读取）
uv run jacara serve --model 模型.gguf            启动本地大模型服务
uv run jacara extract 图片或PDF                  识别、抽取字段并核对出处（要先启动模型服务）
uv run jacara doctor                             检查数据库、队列、对象存储、本地模型是否连得上
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jacara import __version__

if TYPE_CHECKING:
    # 只给类型检查用，运行时不导入：jacara --version 不用加载 OCR 和模型相关的库。
    from jacara.plugins.exporters import DocumentReport
    from jacara.plugins.extractors import FieldSpec, LlmExtractor
    from jacara.plugins.ocr import OcrPage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jacara", description="JacarandaParse 命令行工具")
    parser.add_argument("--version", action="version", version=f"jacara {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_ocr = sub.add_parser("ocr", help="识别图片或 PDF 里的文字")
    p_ocr.add_argument("image", type=Path, help="图片或 PDF 路径")
    p_ocr.add_argument("--engine", default="rapidocr", help="OCR 引擎名，默认 rapidocr")
    p_ocr.add_argument("--det-model", type=Path, help="自定义文字检测模型（.onnx）")
    p_ocr.add_argument(
        "--rec-model", type=Path, help="自定义文字识别模型（.onnx），如微调的手写模型"
    )
    p_ocr.add_argument("--rec-dict", type=Path, help="自定义识别模型的字典文件")
    p_ocr.add_argument("--device", choices=["cpu", "cuda", "coreml"], default="cpu")
    p_ocr.add_argument("--no-angle-cls", action="store_true", help="关闭文字方向纠正")

    p_serve = sub.add_parser("serve", help="启动本地大模型服务（llama-server），Ctrl + C 停止")
    p_serve.add_argument("--model", type=Path, required=True, help="模型文件（.gguf）")
    p_serve.add_argument("--port", type=int, default=8080, help="端口，默认 8080")
    p_serve.add_argument("--context", type=int, default=8192, help="上下文长度，默认 8192")

    p_ext = sub.add_parser("extract", help="识别图片或 PDF 并抽取字段（要先运行 jacara serve）")
    p_ext.add_argument("image", type=Path, help="图片或 PDF 路径")
    p_ext.add_argument("--server", default="http://127.0.0.1:8080", help="模型服务地址")
    p_ext.add_argument("--show-prompt", action="store_true", help="同时打印发给模型的内容")
    p_ext.add_argument(
        "--fields",
        choices=["receipt", "full"],
        default="receipt",
        help="抽哪些字段：receipt 常用 4 个（默认），full 收据上所有单个字段",
    )
    p_ext.add_argument("--excel", type=Path, help="同时把结果导出成 Excel，例如 results/收据.xlsx")
    p_ext.add_argument("--save", action="store_true", help="同时把结果存进数据库")

    sub.add_parser("doctor", help="检查数据库、任务队列、对象存储、本地模型是否连得上")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "ocr":
        return _ocr(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "extract":
        return _extract(args)

    parser.print_help()
    return 0


def _ocr(args: argparse.Namespace) -> int:
    image: Path = args.image
    for path in (image, args.det_model, args.rec_model, args.rec_dict):
        if path is not None and not path.exists():
            print(f"找不到文件：{path}")
            return 1

    from jacara.plugins.ocr import OcrConfig, create_ocr_engine

    config = OcrConfig(
        engine=args.engine,
        det_model=args.det_model,
        rec_model=args.rec_model,
        rec_dict=args.rec_dict,
        device=args.device,
        use_angle_cls=not args.no_angle_cls,
    )
    try:
        engine = create_ocr_engine(config)
    except ValueError as err:
        print(err)
        return 1

    from jacara.plugins.ocr.loader import UnsupportedFileError, load_pages

    try:
        pages = load_pages(image, engine)
    except UnsupportedFileError as err:
        print(err)
        return 1

    for number, page in enumerate(pages, start=1):
        if len(pages) > 1:
            print(f"\n===== 第 {number} / {len(pages)} 页 =====")
        size = f"{page.width}×{page.height}"
        print(f"图片 {size} · 识别出 {len(page.lines)} 行 · 用时 {page.duration_ms} 毫秒")
        print("模型：" + " · ".join(f"{k}={v}" for k, v in page.models.items()) + "\n")
        for i, line in enumerate(page.lines, start=1):
            x0, y0, x1, y1 = (round(v) for v in line.bbox)
            print(f"{i:>3}. [{line.confidence:.2f}] ({x0},{y0})-({x1},{y1})  {line.text}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    model: Path = args.model
    if not model.exists():
        print(f"找不到模型文件：{model}")
        return 1

    from jacara.plugins.model_backends.server import run_server

    print(f"启动模型服务：{model.name} → http://127.0.0.1:{args.port}（Ctrl + C 停止）")
    try:
        return run_server(model, port=args.port, context=args.context)
    except FileNotFoundError as err:
        print(err)
        return 1


def _extract(args: argparse.Namespace) -> int:
    path: Path = args.image
    if not path.exists():
        print(f"找不到文件：{path}")
        return 1

    from jacara.plugins.extractors import PRESETS, ExtractionError, LlmExtractor
    from jacara.plugins.model_backends import ModelBackendError, ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.plugins.ocr.loader import UnsupportedFileError, load_pages

    try:
        pages = load_pages(path, create_ocr_engine())
    except UnsupportedFileError as err:
        print(err)
        return 1

    extractor = LlmExtractor(create_model_backend(ModelConfig(base_url=args.server)))
    reports = []
    for number, page in enumerate(pages, start=1):
        source = path.name
        if len(pages) > 1:
            source = f"{path.name} 第 {number} 页"
            print(f"\n===== 第 {number} / {len(pages)} 页 =====")
        try:
            report = _extract_page(page, source, PRESETS[args.fields], extractor, args)
            reports.append(replace(report, page=number))
        except (ModelBackendError, ExtractionError) as err:
            print(err)
            return 1

    if args.excel is not None:
        from jacara.plugins.exporters import create_exporter

        create_exporter("excel").export(reports, args.excel)
        print(f"\n已导出：{args.excel}")

    if args.save:
        return _save(path, reports)
    return 0


def _save(path: Path, reports: list[DocumentReport]) -> int:
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from jacara.core.db import session_scope
    from jacara.pipeline.store import save_document

    try:
        with session_scope() as session:
            document = save_document(session, path, reports)
    except OperationalError:
        print("连不上数据库。运行 docker compose up -d 启动，再用 uv run jacara doctor 检查")
        return 1
    except ProgrammingError:
        print("数据库里还没有表。先运行：uv run alembic upgrade head")
        return 1
    print(f"\n已存入数据库：documents.id = {document.id}")
    return 0


def _extract_page(
    page: OcrPage,
    source: str,
    fields: Sequence[FieldSpec],
    extractor: LlmExtractor,
    args: argparse.Namespace,
) -> DocumentReport:
    """一页走完 抽取 → 对齐 → 校验，打印结果，返回这一页的完整报告。"""
    from jacara.plugins.aligners import TextMatchAligner
    from jacara.plugins.exporters import DocumentReport
    from jacara.plugins.extractors.llm import build_messages
    from jacara.plugins.validators import RuleValidator

    how = page.models.get("source", "OCR 识别")
    print(f"读取：{how} · {len(page.lines)} 块文字 · 用时 {page.duration_ms} 毫秒")

    if args.show_prompt:
        for message in build_messages(page, fields):
            print(f"\n----- {message.role} -----\n{message.content}")
        print("-----\n")

    result = extractor.extract(page, fields)
    print(f"抽取：模型 {result.model} · 提示词 {result.prompt_version}", end="")
    print(f" · 用时 {result.duration_ms} 毫秒\n")

    aligned = TextMatchAligner().align(page, result)
    checks = RuleValidator().validate(result.fields)
    flagged = checks.fields_with_issues()

    width = max(len(a.field.name) for a in aligned)
    indent = " " * (width + 4)
    for a in aligned:
        name = a.field.name
        value = "（找不到）" if a.field.value is None else " ".join(str(a.field.value).split())
        if a.status == "not_found":
            mark, color = "✗", _RED
        elif a.needs_review or name in flagged:
            mark, color = "!", _YELLOW
        elif a.status == "empty":
            mark, color = "－", ""
        else:
            mark, color = "✓", _GREEN
        print(_paint(f"{mark} {name:<{width}}  {value}", color))

        if a.evidence is not None:
            x0, y0, x1, y1 = (round(v) for v in a.evidence.bbox)
            where = f"第 {a.evidence.row} 行 · 置信度 {a.evidence.confidence:.2f}"
            print(f"{indent}{where} · 位置 ({x0},{y0})-({x1},{y1})")
        if a.needs_review:
            print(_paint(f"{indent}需要确认：{a.note}", color))
        for issue in checks.issues:
            if name in issue.fields:
                print(_paint(f"{indent}规则不通过：{issue.title}", color))

    print("\n规则校验")
    for issue in checks.issues:
        mark, color = ("✗", _RED) if issue.severity == "error" else ("!", _YELLOW)
        print(_paint(f"  {mark} {issue.title}：{issue.message}", color))
    summary = f"  {len(checks.passed)} 条通过，{len(checks.issues)} 条不通过"
    if checks.skipped:
        summary += f"，{len(checks.skipped)} 条因缺字段跳过"
    print(summary)

    reviews = sum(a.needs_review or a.field.name in flagged for a in aligned)
    print(f"\n{len(aligned)} 个字段，{reviews} 个需要人工确认")

    return DocumentReport(
        source=source,
        extraction=result,
        aligned=aligned,
        checks=checks,
        ocr_engine=page.models.get("engine", page.engine),
        processed_at=datetime.now(),
        text_source=page.engine if page.engine == "pdf-text" else "ocr",
        template=args.fields,
    )


def _doctor() -> int:
    from jacara.core.config import get_settings
    from jacara.core.health import run_checks

    checks = run_checks(get_settings())
    width = max(len(c.name) for c in checks)
    for check in checks:
        mark, color = ("✓", _GREEN) if check.ok else ("✗", _RED)
        print(_paint(f"{mark} {check.name:<{width}}  {check.detail}", color))
        if check.hint:
            print(f"  {' ' * width}  → {check.hint}")
    failed = sum(not c.ok for c in checks)
    print(
        f"\n{len(checks)} 项检查，{failed} 项失败" if failed else f"\n{len(checks)} 项检查全部通过"
    )
    return 1 if failed else 0


_RED, _YELLOW, _GREEN = "31", "33", "32"


def _paint(text: str, color: str) -> str:
    """在终端里给文字上色（ANSI 颜色码）。输出到文件时不上色。"""
    if not color or not sys.stdout.isatty():
        return text
    return f"\033[{color}m{text}\033[0m"


if __name__ == "__main__":
    raise SystemExit(main())
