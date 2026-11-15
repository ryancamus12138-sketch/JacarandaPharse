"""Command-line entry point: `jacara` (run through `uv run`).

Usage:
  jacara --version
  jacara ocr FILE                            Recognize text (uses the PDF text layer if present)
  jacara serve --model MODEL.gguf            Start the local LLM server
  jacara extract FILE                        Recognize, extract fields, locate evidence
                                             (requires the model server)
  jacara templates                           List templates
  jacara doctor                              Check database, queue, object storage, local model
  jacara start [--model MODEL.gguf]          Start model, workers and web service; open the
                                             browser (default model: the only .gguf in models/)
  jacara stop                                Stop everything started by `jacara start`
  jacara desktop                             Run all services for the desktop app (data directory:
                                             JACARA_HOME)
  jacara backfill-ocr                        Store OCR results for documents processed by
                                             older versions
  jacara api [--reload]                      Start the web service only
  jacara worker                              Start the background worker only
  jacara user add NAME --role admin          Create a user (first administrator in team mode)
  jacara user add NAME --allow value:review  Create a member with "edit and confirm values"
  jacara user password NAME                  Reset a password (server operators only)
  jacara eval download                       Download the SROIE evaluation set (requires network)
  jacara eval sroie --limit 20               Evaluate the first 20 SROIE receipts
                                             (requires the model server)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jacara import __version__
from jacara.core.config import data_home
from jacara.core.i18n import render

if TYPE_CHECKING:
    # Imported for type checking only, so that `jacara --version` does not load OCR and model
    # libraries.
    import subprocess

    from jacara.core.config import Settings
    from jacara.plugins.exporters import DocumentReport
    from jacara.plugins.ocr import OcrPage


def main(argv: list[str] | None = None) -> int:
    from jacara.core.permissions import GRANTABLE

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
    p_serve.add_argument("--context", type=int, default=32768, help="上下文长度，默认 32768")
    p_serve.add_argument(
        "--mmproj",
        type=Path,
        help="视觉模型文件（mmproj-*.gguf），给了模型就能看原图。不写就用 models/ 里的",
    )

    p_ext = sub.add_parser("extract", help="识别图片或 PDF 并抽取字段（要先运行 jacara serve）")
    p_ext.add_argument("image", type=Path, help="图片或 PDF 路径")
    p_ext.add_argument("--server", default="http://127.0.0.1:8080", help="模型服务地址")
    p_ext.add_argument("--show-prompt", action="store_true", help="同时打印发给模型的内容")
    p_ext.add_argument(
        "--template",
        default="receipt",
        help="用哪个模板：receipt 常用 5 个字段（默认），receipt_full 收据上全部 15 个字段",
    )
    p_ext.add_argument("--excel", type=Path, help="同时把结果导出成 Excel，例如 results/收据.xlsx")
    p_ext.add_argument(
        "--split",
        default="auto",
        help="文件里有几张单据：auto 自动判断（默认）、one 整个文件一张、pages 每页一张，"
        "或 1-2,3,4-6",
    )
    p_ext.add_argument("--save", action="store_true", help="同时把结果存进数据库")

    sub.add_parser("templates", help="列出所有模板（backend/src/jacara/templates/*.toml）")

    sub.add_parser("doctor", help="检查数据库、任务队列、对象存储、本地模型是否连得上")
    p_setup = sub.add_parser(
        "setup",
        help="第一次安装：生成 .env、启动数据库等、建表、检查模型（可以重复运行）",
    )
    p_setup.add_argument(
        "--no-docker", action="store_true", help="不启动 Docker（数据库等已经用别的办法跑起来了）"
    )

    p_api = sub.add_parser("api", help="启动 Web 服务（API 和接口文档）")
    p_api.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    p_api.add_argument("--reload", action="store_true", help="改了代码自动重启（开发用）")

    p_worker = sub.add_parser(
        "worker", help="启动后台任务：处理通过 API 上传的文件（要先运行 jacara serve）"
    )
    p_worker.add_argument(
        "--stage",
        choices=["all", "read", "extract"],
        default="all",
        help="做哪一段：all 从头做到尾（默认）；read 只认字；extract 只调模型抽取",
    )

    p_watch = sub.add_parser(
        "watch", help="监视文件夹，放进去的图片和 PDF 自动导入（Ctrl + C 停止）"
    )
    p_watch.add_argument(
        "folder", type=Path, nargs="?", help="要监视的文件夹；不写就用 .env 里的 JACARA_WATCH_DIR"
    )
    p_watch.add_argument("--template", help="用哪个模板（我的模板的 id）；不写就用上一次用的")
    p_watch.add_argument(
        "--split", default="auto", help="文件里有几张单据：auto（默认）、one、pages 或 1-2,3"
    )
    p_watch.add_argument("--user", help="团队模式：导入的文件算谁传的（用户名）")

    p_start = sub.add_parser("start", help="一起启动模型、后台任务和 Web 服务（日常使用）")
    p_start.add_argument("--port", type=int, default=8000, help="Web 服务的端口，默认 8000")
    p_start.add_argument(
        "--model",
        type=Path,
        help="模型文件（.gguf）。不写就用 .env 的 JACARA_MODEL，或 models/ 里唯一的 .gguf",
    )
    p_start.add_argument(
        "--no-model", action="store_true", help="不启动模型（模型已经在别处运行时用）"
    )
    p_start.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    sub.add_parser("stop", help="停掉 jacara start 启动的全部（模型、后台任务、Web 服务）")
    sub.add_parser(
        "desktop",
        help="桌面版使用：以 JACARA_HOME 为数据目录启动全部服务（由桌面程序调用）",
    )
    p_backup = sub.add_parser(
        "backup", help="备份：数据库和对象存储里的文件打进一个包（先 jacara stop）"
    )
    p_backup.add_argument("--out", type=Path, default=Path("backups"), help="放到哪里")
    p_restore = sub.add_parser("restore", help="把备份包恢复到空的数据库和存储里，再逐样核对")
    p_restore.add_argument("archive", type=Path, help="备份包（.tar）")
    p_drill = sub.add_parser(
        "restore-drill", help="恢复演练：另起一套空环境，恢复进去、逐样打开，做完删掉（要 Docker）"
    )
    p_drill.add_argument("archive", type=Path, help="备份包（.tar）")
    p_drill.add_argument("--keep", action="store_true", help="演练完留着那套环境，方便自己看")
    sub.add_parser(
        "backfill-ocr",
        help="给更新前处理的文档补上 OCR 结果，不用重新上传（不改抽取结果和审核记录）",
    )

    p_eval = sub.add_parser("eval", help="评测：在有标准答案的数据集上算准确率（开发用）")
    eval_sub = p_eval.add_subparsers(dest="eval_command", required=True)
    p_dl = eval_sub.add_parser("download", help="下载 SROIE 评测集（要联网、要装 git）")
    p_dl.add_argument("--data", type=Path, default=Path("datasets/sroie"), help="放到哪里")
    p_sroie = eval_sub.add_parser("sroie", help="在 SROIE 收据上评测（要先运行 jacara serve）")
    p_sroie.add_argument("--data", type=Path, default=Path("datasets/sroie"), help="数据集在哪里")
    p_sroie.add_argument("--limit", type=int, help="只评前 N 张，先少量试跑")
    p_sroie.add_argument("--out", type=Path, default=Path("results/eval"), help="结果放在哪里")
    p_sroie.add_argument("--server", default="http://127.0.0.1:8080", help="模型服务地址")
    p_pairs = eval_sub.add_parser("pairs", help="成对测评：同一张单据的照片和 PDF，比较几种读法")
    p_pairs.add_argument(
        "--manifest", type=Path, default=Path("data/eval/pairs.toml"), help="成对样本清单"
    )
    p_pairs.add_argument(
        "--server", help="模型服务地址；不写就把标准答案当回答（oracle，只量 OCR 和核对）"
    )
    p_pairs.add_argument("--out", type=Path, default=Path("data/eval/results"), help="结果放在哪里")
    p_pairs.add_argument("--docs", help="只跑这几份文档（id，用逗号分开）")
    p_pairs.add_argument("--arms", help="只跑这几种读法（例如 AB），默认 A～E 全跑")
    p_pairs.add_argument(
        "--part", type=Path, help="分批跑：这一批的结果存到这个文件，最后用 --merge 合并"
    )
    p_pairs.add_argument("--merge", type=Path, help="合并这个文件夹里分批跑的结果，出总表")
    p_pairs.add_argument(
        "--learn",
        type=Path,
        help="导出的审核数据里的 train.jsonl：用别的单据人改过的例子，看能不能少错",
    )

    p_replay = eval_sub.add_parser(
        "replay",
        help="回放：审核完成的单据用当时的 OCR 快照换一种设置再抽，和人审核过的值比",
    )
    p_replay.add_argument(
        "--b", required=True, help="要试的一边：模型服务地址，例如 http://127.0.0.1:8080"
    )
    p_replay.add_argument(
        "--a", default="stored", help="对照的一边：stored（当时存下的结果，默认）或模型服务地址"
    )
    p_replay.add_argument("--a-label", help="A 边叫什么（写进结果），默认按设置生成")
    p_replay.add_argument("--b-label", help="B 边叫什么")
    learn_help = "带上以前人改过的例子：recent（默认，最近改得多的）或 curated（整理过的）"
    p_replay.add_argument(
        "--a-learn",
        nargs="?",
        const="recent",
        choices=["recent", "curated"],
        help="A 边" + learn_help,
    )
    p_replay.add_argument(
        "--b-learn",
        nargs="?",
        const="recent",
        choices=["recent", "curated"],
        help="B 边" + learn_help,
    )
    p_replay.add_argument("--template", help="只回放用这个模板的（模板 id 或内置模板名）")
    p_replay.add_argument("--docs", help="只回放这几份文档（id，用逗号分开）")
    p_replay.add_argument("--limit", type=int, help="最多回放几份，先少量试跑")
    p_replay.add_argument(
        "--split", choices=["train", "val", "test_locked"], help="只回放这一边的业务单据"
    )
    p_replay.add_argument(
        "--reread",
        action="store_true",
        help="没有 OCR 快照的（这次更新前处理的）重新认字；差别里会混着 OCR 的变化",
    )
    p_replay.add_argument(
        "--out", type=Path, default=Path("data/eval/replay"), help="结果放在哪里（只在本机）"
    )

    p_ocr_eval = eval_sub.add_parser(
        "ocr",
        help="OCR 模型评测：在数据集上比较两个识别（或检测）模型",
    )
    source = p_ocr_eval.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--dataset", help="数据集库里的数据集：名字，或 名字@版本（不写版本用最新的）"
    )
    source.add_argument(
        "--dataset-file",
        type=Path,
        help="本机的数据集 zip（PaddleOCR / PaddleX 格式），不进数据集库",
    )
    p_ocr_eval.add_argument(
        "--kind",
        choices=["ocr_rec", "ocr_det"],
        default="ocr_rec",
        help="--dataset-file 是识别还是检测",
    )
    p_ocr_eval.add_argument(
        "--split",
        choices=["val", "test", "train", "all"],
        default="val",
        help="评哪一边，默认验证集",
    )
    model_help = "current（现在用的）、builtin（RapidOCR 自带的），或模型库里的 名字@v版本号"
    p_ocr_eval.add_argument("--a", default="current", help="对照的一边：" + model_help)
    p_ocr_eval.add_argument("--b", required=True, help="要试的一边：" + model_help)
    p_ocr_eval.add_argument(
        "--out", type=Path, default=Path("results/eval"), help="结果放在哪里（只在本机）"
    )

    p_models = sub.add_parser("models", help="模型库：离线导入权重、列出版本")
    models_sub = p_models.add_subparsers(dest="models_command", required=True)
    models_sub.add_parser("list", help="列出模型库里的模型和版本")
    p_import = models_sub.add_parser(
        "import", help="把本机的权重文件（例如从 U 盘拷来的）导入模型库，成为新版本"
    )
    p_import.add_argument("file", type=Path, help="权重文件：.gguf 或 .onnx")
    p_import.add_argument("--name", required=True, help="模型名（字母、数字、._-）")
    p_import.add_argument(
        "--kind",
        required=True,
        choices=["model", "mmproj", "ocr_det", "ocr_rec", "table"],
        help="model 大模型、mmproj 视觉模型、ocr_det 文字检测、ocr_rec 文字识别、table 表格结构",
    )
    p_import.add_argument("--dict", type=Path, help="识别模型的字典（.txt）；模型自带字符表时不用")
    p_import.add_argument("--base-model", help="从哪个模型微调来的")
    p_import.add_argument("--license", help="许可，例如 apache-2.0")
    p_import.add_argument("--description", default="", help="说明：怎么训练的、用了什么数据")

    p_user = sub.add_parser("user", help="管理用户（团队模式）")
    user_sub = p_user.add_subparsers(dest="user_command", required=True)
    p_add = user_sub.add_parser("add", help="新建用户，密码会提示输入")
    p_add.add_argument("username", help="登录用的名字（英文字母、数字、_ . -）")
    p_add.add_argument(
        "--role", choices=["admin", "member"], default="member", help="admin 管理员，member 使用者"
    )
    p_add.add_argument(
        "--allow",
        action="append",
        default=[],
        choices=sorted(GRANTABLE),
        help="给使用者开通的权限，可以写多次",
    )
    p_add.add_argument("--name", default="", help="显示名，例如 张三")
    p_pw = user_sub.add_parser("password", help="重设密码，并让这个用户已登录的全部失效")
    p_pw.add_argument("username")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "setup":
        from jacara.core.config import PROJECT_ROOT
        from jacara.ops.setup import run_setup

        return run_setup(PROJECT_ROOT, docker=not args.no_docker)
    if args.command == "templates":
        return _templates()
    if args.command == "api":
        return _api(args)
    if args.command == "worker":
        return _worker(args.stage)
    if args.command == "watch":
        return _watch(args)
    if args.command == "start":
        return _start(args)
    if args.command in ("backup", "restore", "restore-drill"):
        return _backup_commands(args)
    if args.command == "stop":
        return _stop_running()
    if args.command == "desktop":
        from jacara.desktop import run_desktop

        return run_desktop()
    if args.command == "backfill-ocr":
        return _backfill_ocr()
    if args.command == "user":
        return _user(args)
    if args.command == "models":
        return _models(args)
    if args.command == "eval":
        return _eval(args)
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
        print(render(str(err)))
        return 1

    from jacara.plugins.ocr.loader import UnsupportedFileError, load_pages

    try:
        pages = load_pages(image, engine)
    except UnsupportedFileError as err:
        print(render(str(err)))
        return 1

    for number, page in enumerate(pages, start=1):
        if len(pages) > 1:
            print(f"\n===== 第 {number} / {len(pages)} 页 =====")
        size = f"{page.width}×{page.height}"
        print(f"图片 {size} · 识别出 {len(page.lines)} 行 · 用时 {page.duration_ms} 毫秒")
        print("模型：" + " · ".join(f"{k}={render(v)}" for k, v in page.models.items()) + "\n")
        for i, line in enumerate(page.lines, start=1):
            x0, y0, x1, y1 = (round(v) for v in line.bbox)
            print(f"{i:>3}. [{line.confidence:.2f}] ({x0},{y0})-({x1},{y1})  {line.text}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    model: Path = args.model
    if not model.exists():
        print(f"找不到模型文件：{model}")
        return 1

    from jacara.core.config import get_settings
    from jacara.plugins.model_backends.server import run_server

    mmproj: Path | None = args.mmproj or get_settings().find_mmproj()
    if mmproj is not None and not mmproj.is_file():
        print(f"找不到视觉模型文件：{mmproj}")
        return 1
    print(f"启动模型服务：{model.name} → http://127.0.0.1:{args.port}（Ctrl + C 停止）")
    if mmproj is not None:
        print(f"视觉模型：{mmproj.name}（模型能看原图）")
    try:
        return run_server(model, port=args.port, context=args.context, mmproj=mmproj)
    except FileNotFoundError as err:
        print(render(str(err)))
        return 1


def _extract(args: argparse.Namespace) -> int:
    path: Path = args.image
    if not path.exists():
        print(f"找不到文件：{path}")
        return 1

    from jacara.pipeline.run import Pipeline, page_label
    from jacara.plugins.extractors import ExtractionError, LlmExtractor
    from jacara.plugins.extractors.llm import build_messages
    from jacara.plugins.model_backends import ModelBackendError, ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.plugins.ocr.loader import UnsupportedFileError
    from jacara.templates import TemplateError, load_template

    try:
        template = load_template(args.template)
    except TemplateError as err:
        print(render(str(err)))
        return 1

    from jacara.core.config import model_api_key
    from jacara.plugins.ocr.loader import page_images
    from jacara.structure.analyze import analyze_page
    from jacara.structure.engines import default_engines

    backend = create_model_backend(ModelConfig(base_url=args.server, api_key=model_api_key()))
    pipeline = Pipeline(
        ocr=create_ocr_engine(), extractor=LlmExtractor(backend), structure=default_engines()
    )
    try:
        pages = pipeline.read(path)
    except UnsupportedFileError as err:
        print(render(str(err)))
        return 1
    images = page_images(path) if pipeline.needs_images() else [None] * len(pages)

    from jacara.pipeline.pages import stack_images, stack_pages, stacked_engines
    from jacara.pipeline.segment import SplitError, normalize_split, segments

    try:
        judge = getattr(pipeline.extractor, "continues", None)
        parts = segments(pages, normalize_split(args.split), judge)
    except SplitError as err:
        print(render(str(err)))
        return 1
    except (ModelBackendError, ExtractionError) as err:
        print(render(str(err)))
        return 1
    reports = []
    for n, (first, last) in enumerate(parts, start=1):
        if len(pages) > 1:
            where = f"第 {first} 页" if first == last else f"第 {first}–{last} 页"
            print(f"\n===== 第 {n} / {len(parts)} 张单据（{where}）=====")
        for page in pages[first - 1 : last]:
            how = render(page.models.get("note") or page.models.get("source", "OCR 识别"))
            print(f"读取：{how} · {len(page.lines)} 块文字 · 用时 {page.duration_ms} 毫秒")
        if args.show_prompt:
            stacked = stack_pages(pages[first - 1 : last])
            structure = analyze_page(
                stacked.page,
                stack_images(images[first - 1 : last], stacked),
                stacked_engines(pipeline.structure, stacked),
                page_starts=stacked.starts if last > first else (),
            )
            messages = build_messages(stacked.page, template.specs, template.table_specs, structure)
            for message in messages:
                print(f"\n----- {message.role} -----\n{message.content}")
            print("-----\n")
        source = page_label(path.name, first, last, len(pages))
        try:
            report = pipeline.extract_pages(
                pages[first - 1 : last],
                list(images[first - 1 : last]),
                source,
                first,
                template.name,
            )
        except (ModelBackendError, ExtractionError) as err:
            print(render(str(err)))
            return 1
        _print_report(report)
        reports.append(report)

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


def _print_report(report: DocumentReport) -> None:
    """Print the result of one page: values, evidence, review reasons and rule checks."""
    result, aligned, checks = report.extraction, report.aligned, report.checks
    print(f"抽取：模型 {result.model} · 提示词 {result.prompt_version}", end="")
    print(f" · 用时 {result.duration_ms} 毫秒\n")

    flagged = checks.fields_with_issues()

    width = max(len(a.field.name) for a in aligned)
    indent = " " * (width + 4)
    for a in aligned:
        name = a.field.name
        value = "（找不到）" if a.field.value is None else " ".join(str(a.field.value).split())
        if a.status in ("not_found", "rejected"):
            mark, color = "✗", _RED
        elif a.needs_review or name in flagged:
            mark, color = "!", _YELLOW
        elif a.status == "empty":
            mark, color = "－", ""
        elif a.status == "computed":
            mark, color = "=", _GREEN
        else:
            mark, color = "✓", _GREEN
        print(_paint(f"{mark} {name:<{width}}  {value}", color))

        if a.evidence is not None:
            x0, y0, x1, y1 = (round(v) for v in a.evidence.bbox)
            where = f"第 {a.evidence.row} 行 · 置信度 {a.evidence.confidence:.2f}"
            print(f"{indent}{where} · 位置 ({x0},{y0})-({x1},{y1})")
        if a.needs_review:
            print(_paint(f"{indent}需要确认：{render(a.note)}", color))
        for issue in checks.issues:
            if name in issue.fields:
                print(_paint(f"{indent}规则不通过：{render(issue.title)}", color))

    print("\n规则校验")
    for issue in checks.issues:
        mark, color = ("✗", _RED) if issue.severity == "error" else ("!", _YELLOW)
        print(_paint(f"  {mark} {render(issue.title)}：{render(issue.message)}", color))
    summary = f"  {len(checks.passed)} 条通过，{len(checks.issues)} 条不通过"
    if checks.skipped:
        summary += f"，{len(checks.skipped)} 条因缺字段跳过"
    print(summary)

    reviews = sum(a.needs_review or a.field.name in flagged for a in aligned)
    print(f"\n{len(aligned)} 个字段，{reviews} 个需要人工确认")


def _templates() -> int:
    from jacara.templates import TemplateError, load_template, template_names

    for name in template_names():
        try:
            t = load_template(name)
        except TemplateError as err:
            print(_paint(f"✗ {render(str(err))}", _RED))
            continue
        labels = "、".join(f.label for f in t.fields)
        print(f"{t.name}  第 {t.version} 版  {t.title}")
        print(f"  {len(t.fields)} 个字段：{labels}")
        for table in t.tables:
            columns = "、".join(c.label for c in table.columns)
            print(f"  表格 {table.label}：{columns}")
        print(f"  {len(t.rules)} 条规则：{'、'.join(render(r.title) or r.title for r in t.rules)}")
    return 0


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


def _ask_password() -> str | None:
    """Prompt for a password twice. getpass does not echo and leaves no shell history."""
    from getpass import getpass

    from jacara.core.security import MIN_PASSWORD_LENGTH

    password = getpass("密码：")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"密码至少 {MIN_PASSWORD_LENGTH} 个字符")
        return None
    if getpass("再输入一次：") != password:
        print("两次输入的不一样")
        return None
    return password


def _user(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from pydantic import ValidationError
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from jacara.core.db import session_scope
    from jacara.core.security import hash_password
    from jacara.modules.audit.service import record
    from jacara.modules.auth.repository import AuthRepository
    from jacara.modules.auth.schemas import UserCreate
    from jacara.modules.auth.service import AuthError, add_user, owner_of

    password = _ask_password()
    if password is None:
        return 1
    try:
        with session_scope() as session:
            repo = AuthRepository(session)
            if args.user_command == "add":
                data = UserCreate(
                    username=args.username,
                    password=password,
                    display_name=args.name,
                    role=args.role,
                    permissions=args.allow,
                )
                team = repo.default_team()
                user = add_user(session, team.id, data)
                record(session, owner_of(user), "user:create", "user", user.id, via="cli")
                role = "管理员" if user.role == "admin" else "使用者"
                print(f"已新建用户 {user.username}（{role}），团队：{team.name}")
            else:
                user = repo.user_by_username(args.username)
                if user is None:
                    print(f"找不到用户 {args.username}")
                    return 1
                user.password_hash = hash_password(password)
                repo.revoke_sessions(user.id, datetime.now(UTC))
                record(session, owner_of(user), "user:password", "user", user.id, via="cli")
                print(f"已重设 {user.username} 的密码，之前的登录都已失效")
    except ValidationError as err:
        print(f"输入不对：{err.errors()[0]['msg']}")
        return 1
    except AuthError as err:
        print(render(err.message))
        return 1
    except OperationalError:
        print("连不上数据库。运行 docker compose up -d 启动，再用 uv run jacara doctor 检查")
        return 1
    except ProgrammingError:
        print("数据库里还没有表。先运行：uv run alembic upgrade head")
        return 1
    return 0


def _eval_pairs(args: argparse.Namespace) -> int:
    import tempfile
    from datetime import datetime

    from jacara.eval.pairs import (
        OracleExtractor,
        load_manifest,
        pipeline_factory,
        run_pairs,
        summary,
        write_summary,
    )
    from jacara.pipeline.learn import from_dataset
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.structure.engines import default_engines

    if not args.manifest.exists():
        print(f"找不到清单：{args.manifest}（格式见 backend/src/jacara/eval/pairs.py）")
        return 1
    docs = load_manifest(args.manifest)
    mode = "llm" if args.server else "oracle"
    if args.merge:
        from jacara.eval.pairs import merge_parts

        parts = sorted(args.merge.glob("part-*.json"))
        result = summary(merge_parts(parts), docs, mode)
        md = write_summary(result, args.merge)
        print(md.read_text(encoding="utf-8"))
        return 0
    if args.docs:
        wanted = set(args.docs.split(","))
        docs = [d for d in docs if d.id in wanted]
    oracle: OracleExtractor | None = None
    if args.server:
        from jacara.core.config import model_api_key
        from jacara.plugins.extractors import LlmExtractor
        from jacara.plugins.model_backends import ModelConfig, create_model_backend

        backend = create_model_backend(ModelConfig(base_url=args.server, api_key=model_api_key()))
        extractor: object = LlmExtractor(backend)
    else:
        oracle = OracleExtractor()
        extractor = oracle
    make = pipeline_factory(create_ocr_engine(), extractor, default_engines())
    print(f"成对测评 {len(docs)} 份文档（{'模型' if args.server else '标准答案当回答'}）")
    with tempfile.TemporaryDirectory() as scratch:
        results = run_pairs(
            docs,
            make,
            oracle,
            Path(scratch),
            lambda m: print("  " + m),
            list(args.arms or "ABCDE"),
            from_dataset(args.learn) if args.learn else None,
        )
    if args.part:
        from jacara.eval.pairs import save_part

        args.part.parent.mkdir(parents=True, exist_ok=True)
        save_part(results, args.part)
        print(f"这一批的结果在 {args.part}")
        return 0
    result = summary(results, docs, mode)
    md = write_summary(result, args.out / datetime.now().strftime("%Y%m%d-%H%M%S"))
    print(md.read_text(encoding="utf-8"))
    print(f"结果在 {md.parent}")
    return 0


def _eval_replay(args: argparse.Namespace) -> int:
    import uuid
    from datetime import datetime

    from jacara.core.config import model_api_key
    from jacara.core.db import get_session_factory
    from jacara.core.storage import get_storage
    from jacara.eval.replay import Arm, Options, replay, write_result
    from jacara.pipeline.run import Pipeline
    from jacara.plugins.extractors import LlmExtractor
    from jacara.plugins.model_backends import ModelConfig, create_model_backend
    from jacara.structure.engines import default_engines

    def arm(name: str, server: str | None, learn: str | None, label: str | None) -> Arm:
        shown = label or ("当时存下的结果" if server is None else server)
        return Arm(name, shown + (f"（带例子：{learn}）" if learn else ""), server, learn)

    a = arm("A", None if args.a == "stored" else args.a, args.a_learn, args.a_label)
    b = arm("B", args.b, args.b_learn, args.b_label)
    if a.stored and a.learn:
        print("--a-learn 只能用在重新跑的一边（--a 写模型服务地址）")
        return 1
    try:
        docs = [uuid.UUID(x) for x in args.docs.split(",")] if args.docs else None
    except ValueError:
        print("--docs 要写文档 id，用逗号分开")
        return 1
    structure = default_engines()
    ocr = None
    if args.reread:
        from jacara.plugins.ocr import create_ocr_engine

        ocr = create_ocr_engine()

    def pipeline(side: Arm) -> Pipeline:
        backend = create_model_backend(
            ModelConfig(base_url=side.server or "", api_key=model_api_key())
        )
        return Pipeline(ocr=ocr or _NoOcr(), extractor=LlmExtractor(backend), structure=structure)

    options = Options(args.template, docs, args.limit, args.split, args.reread)
    print(f"回放：A {a.label} 对 B {b.label}（只读，不改审核数据）")
    result = replay(
        get_session_factory(), get_storage(), (a, b), pipeline, options, lambda m: print("  " + m)
    )
    if not result.rows:
        print("没有能比的字段。")
        for s in result.skipped[:10]:
            print(f"  跳过 {s.document_id}：{s.reason}")
        return 1
    md = write_result(result, args.out / datetime.now().strftime("%Y%m%d-%H%M%S"))
    print(md.read_text(encoding="utf-8"))
    print(f"结果在 {md.parent}")
    return 0


def _model_ref(text: str) -> dict[str, object]:
    """Parse a model reference: current, builtin, or NAME@vVERSION (the leading v is optional)."""
    import re

    if text in ("current", "builtin"):
        return {"source": text}
    match = re.fullmatch(r"(.+)@v?(\d+)", text)
    if match is None:
        raise ValueError(f"看不懂 {text}：要写 current、builtin，或 名字@v版本号")
    return {"source": "hub", "name": match[1], "version": int(match[2])}


def _eval_ocr(args: argparse.Namespace) -> int:
    from datetime import datetime

    from jacara.core.config import get_settings
    from jacara.eval.ocr import EvalArm, default_engine, run, write_result
    from jacara.modules.hub import datasets as dataset_files
    from jacara.modules.hub.registry import HubError
    from jacara.modules.hub.schemas import ModelRef
    from jacara.modules.hub.service import resolve_arm

    try:
        refs = [ModelRef.model_validate(_model_ref(x)) for x in (args.a, args.b)]
    except ValueError as err:
        print(render(str(err)))
        return 1
    try:
        if args.dataset_file:
            data = args.dataset_file.read_bytes()
            kind, label = args.kind, args.dataset_file.name
        else:
            data, kind, label = _dataset_bytes(args.dataset)
        parsed = dataset_files.parse(data, kind)
        task = "rec" if kind == "ocr_rec" else "det"
        # Resolve "current" and library versions with the same code as the web UI.
        raw = [resolve_arm(get_settings(), n, r, task) for n, r in zip("AB", refs, strict=True)]
    except (HubError, OSError) as err:
        print(render(str(getattr(err, "message", err))))
        return 1
    arms = tuple(
        EvalArm(a["name"], a["label"], a["det"], a["rec"], a["dict"], a["sha256"]) for a in raw
    )
    print(f"OCR 模型评测：{label} 的 {args.split}，A {arms[0].label} 对 B {arms[1].label}")
    try:
        result = run(data, parsed, args.split, (arms[0], arms[1]), default_engine, print)
    except ValueError as err:
        print(render(str(err)))
        return 1
    out = args.out / f"ocr-{datetime.now():%Y%m%d-%H%M%S}"
    md = write_result(result, out, label)
    print(md.read_text(encoding="utf-8"))
    print(f"结果在 {out}（lines.jsonl 里有单据上的文字，只放在本机）")
    return 0


def _dataset_bytes(spec: str) -> tuple[bytes, str, str]:
    """Load a dataset from the library by NAME or NAME@VERSION. The CLI can see datasets of all
    teams."""
    from sqlalchemy import select

    from jacara.core.db import get_session_factory
    from jacara.core.storage import get_storage
    from jacara.modules.hub.models import Dataset
    from jacara.modules.hub.registry import HubError

    name, _, version = spec.rpartition("@") if "@" in spec else (spec, "", "")
    with get_session_factory()() as session:
        query = select(Dataset).where(Dataset.name == name)
        if version:
            query = query.where(Dataset.version == int(version.lstrip("v")))
        found = session.scalars(query.order_by(Dataset.version.desc())).all()
        if not found:
            raise HubError("not_found", f"数据集库里没有 {spec}")
        if len({d.team_id for d in found}) > 1:
            raise HubError(
                "invalid", f"好几个团队都有叫 {name} 的数据集，用 --dataset-file 指定文件"
            )
        dataset = found[0]
        return (
            get_storage().get(dataset.storage_key),
            dataset.kind,
            f"{dataset.name} v{dataset.version}",
        )


def _models(args: argparse.Namespace) -> int:
    from jacara.modules.hub import registry
    from jacara.modules.hub.registry import HubError
    from jacara.modules.hub.service import import_files

    if args.models_command == "list":
        found = registry.versions()
        if not found:
            print("模型库是空的（models/hub/）。")
        for v in found:
            files = "、".join(f"{f.name}（{f.size / 1e6:.1f} MB）" for f in v.files)
            print(f"{v.name} v{v.version}  {registry.KINDS[v.kind]}  {files}")
        return 0
    meta = {
        "base_model": args.base_model,
        "license": args.license,
        "imported_by": "命令行",
    }
    try:
        version = import_files(args.name, args.kind, args.file, args.dict, meta, args.description)
    except (HubError, OSError) as err:
        print(f"导入失败：{render(str(getattr(err, 'message', err)))}")
        return 1
    kind = registry.KINDS[version.kind]
    print(f"已导入 {version.name} v{version.version}（{kind}）：{version.path}")
    for f in version.files:
        print(f"  {f.name}  {f.size} 字节  sha256 {f.sha256}")
    print('在网页"模型和数据集"里点"用这个版本"，或者"换模型"里选它；')
    print("jacara stop、jacara start 后生效。")
    return 0


class _NoOcr:
    """Placeholder OCR engine for replay, which uses OCR snapshots. Being called means a snapshot is
    missing and --reread was not given."""

    name = "none"

    def recognize(self, image: Path) -> OcrPage:
        raise RuntimeError("回放时没有 OCR 快照：加 --reread 重新认字")


def _eval(args: argparse.Namespace) -> int:
    import subprocess
    from datetime import datetime

    from jacara.eval.sroie import download_sroie, load_sroie

    if args.eval_command == "pairs":
        return _eval_pairs(args)
    if args.eval_command == "replay":
        return _eval_replay(args)
    if args.eval_command == "ocr":
        return _eval_ocr(args)
    if args.eval_command == "download":
        try:
            count = download_sroie(args.data)
        except (RuntimeError, subprocess.CalledProcessError) as err:
            print(f"下载失败：{err}")
            return 1
        print(f"SROIE 已在 {args.data}，共 {count} 张")
        return 0

    try:
        samples = load_sroie(args.data, args.limit)
    except FileNotFoundError as err:
        print(render(str(err)))
        return 1

    from jacara.core.config import model_api_key
    from jacara.eval.run import run_eval, write_result
    from jacara.pipeline.run import Pipeline
    from jacara.plugins.extractors import LlmExtractor
    from jacara.plugins.model_backends import ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.structure.engines import default_engines

    backend = create_model_backend(ModelConfig(base_url=args.server, api_key=model_api_key()))
    pipeline = Pipeline(
        ocr=create_ocr_engine(), extractor=LlmExtractor(backend), structure=default_engines()
    )

    def progress(done: int, total: int, sample_id: str) -> None:
        print(f"\r{done}/{total}  {sample_id}", end="", flush=True)

    print(f"评测 {len(samples)} 张收据（Ctrl + C 中止）")
    result = run_eval(samples, pipeline.run, progress)
    out = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
    write_result(result, out, "SROIE")
    print("\n\n" + (out / "summary.md").read_text(encoding="utf-8"))
    print(f"结果：{out}")
    if result.failures:
        print(f"{len(result.failures)} 张出错，原因见 summary.json 的 failures")
    return 0


def _infra_problems(settings: Settings) -> list[tuple[str, str]]:
    """Preflight checks: database and queue reachable, database at the latest revision. Returns
    (problem, remedy) pairs."""
    from jacara.core.health import check_postgres, check_valkey

    docker = "打开 Docker Desktop（open -a Docker），等它启动好，再运行 docker compose up -d"
    problems = [
        (f"{check.name} 连不上（{check.detail}）", docker)
        for check in (check_postgres(settings), check_valkey(settings))
        if not check.ok
    ]
    if not problems and _migration_behind():
        problems.append(
            ("数据库还不是最新版本（新版本加了表或列）", "运行 uv run alembic upgrade head")
        )
    return problems


def _migration_behind() -> bool:
    """The database revision (alembic_version) differs from the latest migration; run alembic
    upgrade head."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from jacara.core.db import get_engine
    from jacara.core.migrate import alembic_config

    heads = set(ScriptDirectory.from_config(alembic_config()).get_heads())
    with get_engine().connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    return current != heads


def _web_outdated(dist: Path) -> bool:
    """Files in web/src are newer than dist/index.html: the frontend was changed without running npm
    run build."""
    src = dist.parent / "src"
    index = dist / "index.html"
    if not src.is_dir() or not index.is_file():
        return False
    built = index.stat().st_mtime
    return any(f.stat().st_mtime > built for f in src.rglob("*") if f.is_file())


def _backfill_ocr() -> int:
    """jacara backfill-ocr: re-run OCR on original files and store the results. Extracted values and
    review records are not modified."""
    from jacara.core.storage import get_storage
    from jacara.pipeline.backfill import backfill_document, missing_ocr
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.plugins.ocr.loader import load_pages

    ids = missing_ocr()
    if not ids:
        print(_paint("所有文档都有 OCR 结果了，不用补", _GREEN))
        return 0
    print(f"有 {len(ids)} 个文档要补 OCR 结果（每页几秒钟）……")
    storage, engine = get_storage(), create_ocr_engine()
    failed = 0
    for n, document_id in enumerate(ids, start=1):
        try:
            pages = backfill_document(
                document_id, storage=storage, read=lambda path: load_pages(path, engine)
            )
            print(f"  [{n}/{len(ids)}] {document_id}：补了 {pages} 页")
        except Exception as err:  # A failure in one document does not stop the others.
            failed += 1
            print(_paint(f"  [{n}/{len(ids)}] {document_id}：没补上（{render(str(err))}）", _RED))
    print(_paint("完成", _GREEN) if not failed else _paint(f"完成，{failed} 个没补上", _YELLOW))
    print('刷新审核页面，就能用"原文文字"和框选了。')
    return 0 if not failed else 1


def _follow_launcher() -> None:
    """When the desktop launcher started this process (JACARA_WATCH_STDIN=1): make its log useful
    and exit as soon as the launcher is gone (its end of standard input closes)."""
    import os

    from jacara.desktop import WATCH_STDIN, on_stdin_closed

    if os.environ.get(WATCH_STDIN) != "1":
        return
    _log_for_launcher()

    def leave() -> None:
        print("启动器已退出，随之退出", flush=True)
        os._exit(0)

    on_stdin_closed(leave)


def _log_for_launcher() -> None:
    """Make the log file useful when the desktop launcher runs this process:
    - lines are written as they are printed (a file is otherwise block-buffered, and what is still
      buffered is lost if the process dies);
    - a crash in native code (OCR, PDF rendering) writes the Python stack of every thread to the
      log before the process ends (faulthandler, Python standard library);
    - log records at INFO and above go to the log with their time."""
    import faulthandler
    import logging
    import os

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True)
    faulthandler.enable()
    # For a process that stops making progress without output or exit: with
    # JACARA_STACK_DUMP_SECONDS set, the Python stack of every thread is written to the log at that
    # interval (faulthandler.dump_traceback_later, the standard library's tool for hangs; pytest's
    # faulthandler_timeout uses it too). Off unless set; the packaging smoke test sets it.
    every = os.environ.get("JACARA_STACK_DUMP_SECONDS")
    if every:
        faulthandler.dump_traceback_later(float(every), repeat=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


def _worker(stage: str = "all") -> int:
    from jacara.worker import run_worker

    # Exit with the desktop launcher. Documents in progress are marked as interrupted at the next
    # start.
    _follow_launcher()
    names = {"all": "后台任务", "read": "后台任务（OCR 工位）", "extract": "后台任务（模型工位）"}
    print(f"{names[stage]}已启动，等待上传的文件（Ctrl + C 停止）")
    run_worker(stage)  # type: ignore[arg-type]
    return 0


def _watch(args: argparse.Namespace) -> int:
    """Watch folders: images and PDFs added to them are imported automatically. Source files are
    never modified or deleted. Without a folder argument, every folder of the settings is watched,
    each with its own template."""
    import time

    from sqlalchemy import select

    from jacara.core.config import WatchFolder, get_settings
    from jacara.core.db import session_scope
    from jacara.core.owner import PERSONAL_OWNER
    from jacara.core.queue import create_queue
    from jacara.core.storage import get_storage
    from jacara.models import User
    from jacara.modules.auth.service import owner_of
    from jacara.pipeline.segment import SplitError, normalize_split
    from jacara.pipeline.watch import Watcher

    _follow_launcher()
    settings = get_settings()
    if args.folder is not None:
        folders = [
            WatchFolder(path=args.folder, template=args.template or settings.jacara_watch_template)
        ]
    else:
        folders = settings.watch_folders()
    if not folders:
        print("要监视哪个文件夹？写在命令后面，或者在设置页「识别和处理」中添加监视文件夹")
        return 1
    try:
        split = normalize_split(args.split)
    except SplitError as err:
        print(render(str(err)))
        return 1
    owner = PERSONAL_OWNER
    if settings.jacara_mode == "team":
        username = args.user or settings.jacara_watch_user
        if not username:
            print("团队模式要写导入的文件算谁传的：--user 用户名，或 .env 里的 JACARA_WATCH_USER")
            return 1
        with session_scope() as session:
            user = session.scalar(select(User).where(User.username == username.lower()))
            if user is None or not user.is_active:
                print(f"没有叫 {username} 的用户（或者已经停用）")
                return 1
            owner = owner_of(user)
    watchers: list[Watcher] = []
    for folder in folders:
        root = folder.path.expanduser().resolve()
        if not root.is_dir():
            print(f"找不到文件夹：{root}（跳过）")
            continue
        template = args.template if args.folder is None and args.template else folder.template
        watchers.append(
            Watcher(
                root, None, get_storage(), create_queue(), owner, template=template, split=split
            )
        )
        print(f"监视文件夹：{root}（模板 {template}）")
    if not watchers:
        return 1
    print("放进去的图片和 PDF 会自动导入，不动原文件；Ctrl + C 停止")
    from contextlib import suppress

    with suppress(KeyboardInterrupt):
        while True:
            for watcher in watchers:
                for path, result in watcher.scan():
                    print(f"{result}：{path.relative_to(watcher.root)}", flush=True)
            time.sleep(5.0)
    return 0


def _api(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"接口文档：http://127.0.0.1:{args.port}/docs（Ctrl + C 停止）")
    print("只启动了 API；日常使用请用 jacara start，开发网页请在 web/ 里 npm run dev")
    # Bind to localhost only. Reload mode requires an import string ("module:attr") so that uvicorn
    # can re-import after code changes.
    uvicorn.run("jacara.app:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


def _start(args: argparse.Namespace) -> int:
    """Start all services with one command and open the browser.

    The worker and the model server run as separate subprocesses; the API runs in this process.
    On Ctrl + C (or `jacara stop` from another terminal) this function waits for all children
    to exit so that no orphan processes remain.
    """
    import json
    import os
    import signal
    import subprocess
    import threading
    import webbrowser

    import uvicorn

    from jacara.core.config import get_settings

    settings = get_settings()
    weak = settings.weak_secrets()
    if weak:
        print(_paint("不能启动：.env 里这些密钥还是空的或 change-me：" + "、".join(weak), _RED))
        print("用 openssl rand -hex 16 生成随机值填进去。")
        return 1

    # Validate our own arguments (missing model file) before checking ports; the result does not
    # depend on running services.
    model = None if args.no_model else (args.model or settings.find_model())
    if model is not None and not model.exists():
        print(f"找不到模型文件：{model}")
        return 1

    url = f"http://127.0.0.1:{args.port}"
    if not _port_free(args.port):
        if _answers(f"{url}/api/v1/health"):
            # Already running: just open the browser.
            print(_paint(f"JacarandaParse 已经在运行了：{url}", _GREEN))
            print("要重启的话，先运行 uv run jacara stop，再运行 uv run jacara start。")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        print(_paint(f"不能启动：端口 {args.port} 被别的程序占用了", _RED))
        print(f"运行 lsof -i :{args.port} 看是哪个程序（PID 那一列），再 kill 那个 PID。")
        return 1

    # The database and queue run in Docker. Report a stopped Docker here instead of failing later in
    # the browser.
    problems = _infra_problems(settings)
    if problems:
        for problem, fix in problems:
            print(_paint(f"不能启动：{problem}", _RED))
            print(f"  → {fix}")
        return 1

    if model is not None and not _wait_free(MODEL_PORT):
        if _answers(f"http://127.0.0.1:{MODEL_PORT}/health", loading_ok=True):
            print(f"{MODEL_PORT} 端口上已经有模型服务在运行，直接用它")
            model = None
        else:
            print(_paint(f"不能启动模型：端口 {MODEL_PORT} 被别的程序占用了", _RED))
            print(
                f"运行 lsof -i :{MODEL_PORT} 看是哪个程序（PID 那一列），kill 那个 PID 后再启动；"
            )
            print("或者加 --no-model 不启动模型。")
            return 1
    if model is None and _port_free(MODEL_PORT):
        # No model file and no model server on port 8080: start anyway, but every upload will fail
        # to process.
        print(
            _paint(
                f"提醒：没有模型服务（{MODEL_PORT} 端口没人在用），上传的文件会处理失败。", _YELLOW
            )
        )
        print("把模型文件放进 models/ 文件夹，或者加 --model 模型文件.gguf。")

    web = settings.web_dist / "index.html"
    if not web.is_file():
        print(_paint("提醒：网页还没打包，先运行 cd web && npm run build（API 照常启动）", _YELLOW))
    elif _web_outdated(settings.web_dist):
        print(
            _paint(
                "提醒：网页代码改过了，还没重新打包，浏览器里看到的是旧页面。"
                "先 Ctrl + C，运行 cd web && npm run build，再启动",
                _YELLOW,
            )
        )

    children: list[subprocess.Popen[bytes]] = []
    if model is not None:
        from jacara.plugins.model_backends.server import server_command, server_env

        try:
            mmproj = settings.find_mmproj()
            command = server_command(model, port=MODEL_PORT, mmproj=mmproj)
        except FileNotFoundError as err:
            print(render(str(err)))
            return 1
        # Generate a new key on every start so that other local programs (including web pages in the
        # browser) cannot use the model.
        # Pass the key through environment variables to llama-server and the worker, never on the
        # command line (visible in ps).
        key = _new_model_key()
        env = {**server_env(), "LLAMA_API_KEY": key}
        os.environ["MODEL_API_KEY"] = key  # Inherited by the worker started below.
        # Start llama-server directly rather than through jacara serve, so that terminate reaches
        # the server process itself.
        children.append(subprocess.Popen(command, env=env))  # noqa: S603
        print(f"启动模型服务：{model.name} → http://127.0.0.1:{MODEL_PORT}")
        if mmproj is not None:
            print(f"视觉模型：{mmproj.name}（照片、扫描件 OCR 不可靠时让模型看原图）")
    worker = [sys.executable, "-m", "jacara.cli", "worker"]
    if settings.jacara_pipeline:
        # Pipeline: separate processes for the OCR stage and the model stage, running concurrently.
        # Interrupted documents are marked as failed here, before either stage starts; doing it
        # inside each stage could fail work the other stage has already started.
        _reset_interrupted()
        children.append(subprocess.Popen([*worker, "--stage", "read"]))  # noqa: S603
        children.append(subprocess.Popen([*worker, "--stage", "extract"]))  # noqa: S603
    else:
        children.append(subprocess.Popen(worker))  # noqa: S603
    if settings.watch_folders():
        # Watch folders: started only when some are set (settings page or JACARA_WATCH_DIR).
        children.append(subprocess.Popen([sys.executable, "-m", "jacara.cli", "watch"]))
        for folder in settings.watch_folders():
            print(f"监视文件夹：{folder.path}")

    # Record our own and child process ids. jacara stop signals this process first and cleans up
    # children if it is gone.
    RUN_FILE.parent.mkdir(exist_ok=True)
    run = {"main": os.getpid(), "children": [c.pid for c in children]}
    RUN_FILE.write_text(json.dumps(run), encoding="utf-8")
    # Route SIGTERM (kill, shutdown) through the Ctrl + C path. uvicorn re-raises the signal after
    # shutting down;
    # the default handler would exit immediately and skip the finally block below, leaving orphan
    # processes.
    previous = signal.signal(signal.SIGTERM, _interrupt)
    print(f"打开 {url}（Ctrl + C 或 uv run jacara stop 全部停止）")
    if not args.no_browser:
        # Open the browser once the web service is up. The model may need several more seconds to
        # load; uploads queue until it is ready.
        threading.Timer(2.0, webbrowser.open, [url]).start()
    try:
        uvicorn.run("jacara.app:app", host="127.0.0.1", port=args.port)
    except KeyboardInterrupt:
        pass  # Ctrl + C or jacara stop: normal shutdown.
    finally:
        _stop(children)
        RUN_FILE.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, previous)
    return 0


def _reset_interrupted() -> None:
    from jacara.pipeline.process import reset_interrupted

    try:
        count = reset_interrupted()
    # Database not ready yet: the worker reports it when processing; do not block startup.
    except Exception as err:
        print(_paint(f"提醒：没能检查上次中断的文档（{err}）", _YELLOW))
        return
    if count:
        print(_paint(f"{count} 个文档上次处理到一半被中断，已标记为失败，可以重新处理", _YELLOW))


def _new_model_key() -> str:
    """Generate the model server API key and write it to .run/model-key (owner-readable only); CLI
    extraction reads it from there."""
    import secrets

    from jacara.core.config import MODEL_KEY_FILE

    key = secrets.token_urlsafe(32)
    MODEL_KEY_FILE.parent.mkdir(exist_ok=True)
    MODEL_KEY_FILE.touch(mode=0o600, exist_ok=True)
    MODEL_KEY_FILE.chmod(0o600)
    MODEL_KEY_FILE.write_text(key, encoding="utf-8")
    return key


def _interrupt(signum: int, frame: object) -> None:
    """Handle SIGTERM the same way as Ctrl + C."""
    raise KeyboardInterrupt


def _running() -> bool:
    """Whether jacara start is running."""
    import json

    try:
        run = json.loads(RUN_FILE.read_text(encoding="utf-8"))
        return _alive(int(run["main"]))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return False


def _backup_commands(args: argparse.Namespace) -> int:
    from jacara.core.config import get_settings
    from jacara.ops import commands

    if args.command == "restore-drill":
        return commands.run_drill(args.archive, args.keep)
    settings = get_settings()
    if args.command == "restore":
        return commands.run_restore(args.archive, settings, _running())
    from jacara.core.db import get_engine
    from jacara.core.storage import get_storage

    return commands.run_backup(get_engine(), get_storage(), args.out, __version__, _running())


def _stop_running() -> int:
    """jacara stop: ask jacara start to shut down (it stops the model and workers), then clean up
    any remaining children.

    Sends SIGINT, equivalent to Ctrl + C, so that start runs its normal shutdown path. On Windows
    os.kill() can deliver Ctrl + C only to processes sharing the console, so any other signal there
    ends the process at once (TerminateProcess) and the children are stopped below."""
    import json
    import os
    import signal
    import time

    try:
        run = json.loads(RUN_FILE.read_text(encoding="utf-8"))
        main, children = int(run["main"]), [int(p) for p in run["children"]]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        print("没有找到正在运行的 jacara start。")
        return 0

    if _alive(main):
        os.kill(main, signal.SIGINT)
        print("正在停止……")
        for _ in range(60):  # Wait up to 30 seconds; the worker may be processing a file.
            if not _alive(main):
                break
            time.sleep(0.5)
        else:
            print(_paint(f"30 秒还没停下来，可以运行 kill -9 {main}", _YELLOW))
            return 1

    # start stops its children on a normal exit; if it was killed, the children may still be
    # running.
    left = [pid for pid in children if _alive(pid)]
    for pid in left:
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not any(_alive(pid) for pid in left):
            break
        time.sleep(0.5)
    for pid in left:
        if _alive(pid):
            # Windows has no SIGKILL; os.kill() ends the process there whatever the signal.
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    RUN_FILE.unlink(missing_ok=True)
    print(_paint("已经全部停止", _GREEN))
    return 0


def _alive(pid: int) -> bool:
    """Whether the process exists. On POSIX, signal 0 checks existence without affecting the
    process. On Windows os.kill() with any signal but the console ones ends the process
    (TerminateProcess), so the process is opened for querying and its exit code read instead:
    STILL_ACTIVE while it runs."""
    import os
    import sys

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information, still_active, access_denied = 0x1000, 259, 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # Exists but may not be opened (another user's process), or does not exist.
            return ctypes.get_last_error() == access_denied
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists, but belongs to another process.
    return True


def _answers(url: str, *, loading_ok: bool = False) -> bool:
    """Whether the URL responds as expected (tells whether a port is held by our own service).

    loading_ok: llama-server returns 503 from /health while a model is loading; treat that as
    a model server.
    trust_env=False: bypass any system proxy for local addresses (httpx honors it by default).
    """
    import httpx

    try:
        status = httpx.get(url, timeout=3, trust_env=False).status_code
    except httpx.HTTPError:
        return False
    return status == 200 or (loading_ok and status == 503)


def _wait_free(port: int, seconds: float = 10.0) -> bool:
    """Wait for a port to be released; after Ctrl + C the previous model server may need a few
    seconds to exit."""
    import time

    deadline = time.monotonic() + seconds
    while True:
        if _port_free(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _port_free(port: int) -> bool:
    """Whether a local port is free. Checked up front so that a conflict is not discovered only
    after the worker and model have started.

    SO_REUSEADDR (macOS, Linux): a recently closed service leaves the port in TIME_WAIT for a
    minute or two (not visible in lsof). llama-server and uvicorn can still bind in that state
    because they set the same option, so only an active listener counts as in use. On Windows the
    option means something else: it lets a socket bind to a port another socket is using, so the
    check would always pass. There a plain bind is used, which fails while the port is in use
    (sockets are not shareable by default since Windows Server 2003).
    """
    import socket
    import sys

    with socket.socket() as sock:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _stop(children: list[subprocess.Popen[bytes]]) -> None:
    """Stop background processes: terminate first, kill after 10 seconds."""
    import subprocess

    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


MODEL_PORT = 8080
"""Port of the model server started by jacara start; matches the default MODEL_SERVER."""


RUN_FILE = data_home() / ".run" / "jacara.json"
"""Process ids written by jacara start (.run/ is git-ignored); used by jacara stop."""

_RED, _YELLOW, _GREEN = "31", "33", "32"


def _paint(text: str, color: str) -> str:
    """Colorize terminal output with ANSI codes; no color when output is redirected."""
    if not color or not sys.stdout.isatty():
        return text
    return f"\033[{color}m{text}\033[0m"


if __name__ == "__main__":
    raise SystemExit(main())
