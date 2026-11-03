"""命令行入口：`jacara`。

uv run jacara --version
uv run jacara ocr 图片路径                       用默认模型识别文字
uv run jacara serve --model 模型.gguf            启动本地大模型服务
uv run jacara extract 图片路径                   识别文字并抽取字段（要先启动模型服务）
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from jacara import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jacara", description="JacarandaParse 命令行工具")
    parser.add_argument("--version", action="version", version=f"jacara {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_ocr = sub.add_parser("ocr", help="识别一张图片里的文字")
    p_ocr.add_argument("image", type=Path, help="图片路径，支持 jpg、png")
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

    p_ext = sub.add_parser("extract", help="识别图片并抽取字段（要先运行 jacara serve）")
    p_ext.add_argument("image", type=Path, help="图片路径，支持 jpg、png")
    p_ext.add_argument("--server", default="http://127.0.0.1:8080", help="模型服务地址")
    p_ext.add_argument("--show-prompt", action="store_true", help="同时打印发给模型的内容")
    p_ext.add_argument(
        "--fields",
        choices=["receipt", "full"],
        default="receipt",
        help="抽哪些字段：receipt 常用 4 个（默认），full 收据上所有单个字段",
    )
    p_ext.add_argument("--excel", type=Path, help="同时把结果导出成 Excel，例如 results/收据.xlsx")

    args = parser.parse_args(argv)
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
    page = engine.recognize(image)

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
    image: Path = args.image
    if not image.exists():
        print(f"找不到文件：{image}")
        return 1

    from jacara.plugins.aligners import TextMatchAligner
    from jacara.plugins.extractors import PRESETS, ExtractionError, LlmExtractor
    from jacara.plugins.extractors.llm import build_messages
    from jacara.plugins.model_backends import ModelBackendError, ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.plugins.validators import RuleValidator

    fields = PRESETS[args.fields]
    page = create_ocr_engine().recognize(image)
    print(f"OCR：识别出 {len(page.lines)} 块文字 · 用时 {page.duration_ms} 毫秒")

    if args.show_prompt:
        for message in build_messages(page, fields):
            print(f"\n----- {message.role} -----\n{message.content}")
        print("-----\n")

    backend = create_model_backend(ModelConfig(base_url=args.server))
    try:
        result = LlmExtractor(backend).extract(page, fields)
    except (ModelBackendError, ExtractionError) as err:
        print(err)
        return 1

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
            where = f"第 {a.evidence.row} 行 · OCR 置信度 {a.evidence.confidence:.2f}"
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

    if args.excel is not None:
        from jacara.plugins.exporters import DocumentReport, create_exporter

        report = DocumentReport(
            source=image.name,
            extraction=result,
            aligned=aligned,
            checks=checks,
            ocr_engine=page.models.get("engine", page.engine),
            processed_at=datetime.now(),
        )
        create_exporter("excel").export([report], args.excel)
        print(f"已导出：{args.excel}")
    return 0


_RED, _YELLOW, _GREEN = "31", "33", "32"


def _paint(text: str, color: str) -> str:
    """在终端里给文字上色（ANSI 颜色码）。输出到文件时不上色。"""
    if not color or not sys.stdout.isatty():
        return text
    return f"\033[{color}m{text}\033[0m"


if __name__ == "__main__":
    raise SystemExit(main())
