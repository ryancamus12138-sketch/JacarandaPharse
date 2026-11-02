"""命令行入口：`jacara`。

uv run jacara --version
uv run jacara ocr 图片路径                       用默认模型识别
uv run jacara ocr 图片路径 --rec-model 模型.onnx  用自己微调的识别模型
"""

import argparse
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

    args = parser.parse_args(argv)
    if args.command == "ocr":
        return _ocr(args)

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


if __name__ == "__main__":
    raise SystemExit(main())
