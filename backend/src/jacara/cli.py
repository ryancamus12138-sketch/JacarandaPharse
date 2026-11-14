"""命令行入口：`jacara`。

uv run jacara --version
uv run jacara ocr 图片或PDF                      识别文字（PDF 有文字层时直接读取）
uv run jacara serve --model 模型.gguf            启动本地大模型服务
uv run jacara extract 图片或PDF                  识别、抽取字段并核对出处（要先启动模型服务）
uv run jacara templates                          列出所有模板
uv run jacara doctor                             检查数据库、队列、对象存储、本地模型是否连得上
uv run jacara start                              一起启动模型、后台任务和 Web 服务，自动打开浏览器
uv run jacara start --model 模型.gguf            指定模型文件（不写就用 models/ 里唯一的 .gguf）
uv run jacara stop                               停掉 jacara start 启动的全部（在别的终端也行）
uv run jacara backfill-ocr                       给更新前处理的文档补上 OCR 结果，不用重新上传
uv run jacara api                                只启动 Web 服务（开发时加 --reload）
uv run jacara worker                             只启动后台任务，处理通过 API 上传的文件
uv run jacara user add 用户名 --role admin        新建用户（团队模式的第一个管理员用它建）
uv run jacara user add 用户名 --allow value:review    新建使用者，并开通"修改和确认字段"
uv run jacara user password 用户名                重设密码（只给能操作服务器的人应急用）
uv run jacara eval download                      下载 SROIE 评测集（开发用，要联网）
uv run jacara eval sroie --limit 20              在 SROIE 上评测前 20 张（要先启动模型服务）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jacara import __version__

if TYPE_CHECKING:
    # 只给类型检查用，运行时不导入：jacara --version 不用加载 OCR 和模型相关的库。
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
        help="第一次安装：生成 .env、启动数据库等、建表、检查模型（可以重复运行，ADR 0064）",
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
        help="做哪一段（ADR 0051）：all 从头做到尾（默认）；read 只认字；extract 只调模型抽取",
    )

    p_watch = sub.add_parser(
        "watch", help="监视一个文件夹，放进去的图片和 PDF 自动导入（Ctrl + C 停止）"
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
    p_pairs = eval_sub.add_parser(
        "pairs", help="成对测评：同一张单据的照片和 PDF，比较几种读法（ADR 0042）"
    )
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
        help="导出的审核数据里的 train.jsonl：用别的单据人改过的例子（ADR 0045），看能不能少错",
    )

    p_replay = eval_sub.add_parser(
        "replay",
        help="回放：审核完成的单据用当时的 OCR 快照换一种设置再抽，和人审核过的值比（ADR 0053）",
    )
    p_replay.add_argument(
        "--b", required=True, help="要试的一边：模型服务地址，例如 http://127.0.0.1:8080"
    )
    p_replay.add_argument(
        "--a", default="stored", help="对照的一边：stored（当时存下的结果，默认）或模型服务地址"
    )
    p_replay.add_argument("--a-label", help="A 边叫什么（写进结果），默认按设置生成")
    p_replay.add_argument("--b-label", help="B 边叫什么")
    learn_help = (
        "带上以前人改过的例子：recent（默认，最近改得多的）或 curated（整理过的，ADR 0055）"
    )
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
        help="OCR 模型评测：在数据集上比较两个识别（或检测）模型（ADR 0060）",
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

    p_models = sub.add_parser("models", help="模型库（ADR 0059）：离线导入权重、列出版本")
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
        print(err)
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
        print(err)
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
        print(err)
        return 1
    images = page_images(path) if pipeline.needs_images() else [None] * len(pages)

    from jacara.pipeline.pages import stack_images, stack_pages, stacked_engines
    from jacara.pipeline.segment import SplitError, normalize_split, segments

    try:
        judge = getattr(pipeline.extractor, "continues", None)
        parts = segments(pages, normalize_split(args.split), judge)
    except SplitError as err:
        print(err)
        return 1
    except (ModelBackendError, ExtractionError) as err:
        print(err)
        return 1
    reports = []
    for n, (first, last) in enumerate(parts, start=1):
        if len(pages) > 1:
            where = f"第 {first} 页" if first == last else f"第 {first}–{last} 页"
            print(f"\n===== 第 {n} / {len(parts)} 张单据（{where}）=====")
        for page in pages[first - 1 : last]:
            how = page.models.get("note") or page.models.get("source", "OCR 识别")
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
            print(err)
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
    """把一页的结果打印出来：每个字段的值、出处、需要确认的原因，以及规则校验。"""
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


def _templates() -> int:
    from jacara.templates import TemplateError, load_template, template_names

    for name in template_names():
        try:
            t = load_template(name)
        except TemplateError as err:
            print(_paint(f"✗ {err}", _RED))
            continue
        labels = "、".join(f.label for f in t.fields)
        print(f"{t.name}  第 {t.version} 版  {t.title}")
        print(f"  {len(t.fields)} 个字段：{labels}")
        for table in t.tables:
            columns = "、".join(c.label for c in table.columns)
            print(f"  表格 {table.label}：{columns}")
        print(f"  {len(t.rules)} 条规则：{'、'.join(r.title for r in t.rules)}")
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
    """输入两次密码。getpass 输入时屏幕上不显示，也不会留在终端历史里。"""
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
        print(err.message)
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
    """current、builtin，或 名字@v版本号（版本号前的 v 可以不写）。"""
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
        print(err)
        return 1
    try:
        if args.dataset_file:
            data = args.dataset_file.read_bytes()
            kind, label = args.kind, args.dataset_file.name
        else:
            data, kind, label = _dataset_bytes(args.dataset)
        parsed = dataset_files.parse(data, kind)
        task = "rec" if kind == "ocr_rec" else "det"
        # "现在用的"、模型库版本按和网页同一套代码解析
        raw = [resolve_arm(get_settings(), n, r, task) for n, r in zip("AB", refs, strict=True)]
    except (HubError, OSError) as err:
        print(getattr(err, "message", err))
        return 1
    arms = tuple(
        EvalArm(a["name"], a["label"], a["det"], a["rec"], a["dict"], a["sha256"]) for a in raw
    )
    print(f"OCR 模型评测：{label} 的 {args.split}，A {arms[0].label} 对 B {arms[1].label}")
    try:
        result = run(data, parsed, args.split, (arms[0], arms[1]), default_engine, print)
    except ValueError as err:
        print(err)
        return 1
    out = args.out / f"ocr-{datetime.now():%Y%m%d-%H%M%S}"
    md = write_result(result, out, label)
    print(md.read_text(encoding="utf-8"))
    print(f"结果在 {out}（lines.jsonl 里有单据上的文字，只放在本机）")
    return 0


def _dataset_bytes(spec: str) -> tuple[bytes, str, str]:
    """数据集库里的数据集（命令行看得到所有团队的，和回放一样）：名字，或 名字@版本。"""
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
        print(f"导入失败：{getattr(err, 'message', err)}")
        return 1
    kind = registry.KINDS[version.kind]
    print(f"已导入 {version.name} v{version.version}（{kind}）：{version.path}")
    for f in version.files:
        print(f"  {f.name}  {f.size} 字节  sha256 {f.sha256}")
    print('在网页"模型和数据集"里点"用这个版本"，或者"换模型"里选它；')
    print("jacara stop、jacara start 后生效。")
    return 0


class _NoOcr:
    """回放用 OCR 快照，不认字；真叫到了说明没有快照又没加 --reread。"""

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
        print(err)
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
    """启动前检查：数据库、任务队列连得上吗？数据库是不是最新版本？返回（问题，怎么解决）。"""
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
    """数据库的版本（alembic_version）和代码里最新的迁移不一样：要先 alembic upgrade head。"""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from jacara.core.db import get_engine

    ini = Path("alembic.ini")
    if not ini.is_file():
        return False  # 不在项目根目录运行：查不了，不拦
    heads = set(ScriptDirectory.from_config(Config(str(ini))).get_heads())
    with get_engine().connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    return current != heads


def _web_outdated(dist: Path) -> bool:
    """web/src 里有文件比打包好的 dist/index.html 新：改了网页代码却没重新 npm run build。"""
    src = dist.parent / "src"
    index = dist / "index.html"
    if not src.is_dir() or not index.is_file():
        return False
    built = index.stat().st_mtime
    return any(f.stat().st_mtime > built for f in src.rglob("*") if f.is_file())


def _backfill_ocr() -> int:
    """jacara backfill-ocr：重新 OCR 原始文件，把结果补进去。抽取的值和审核记录都不动。"""
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
        except Exception as err:  # 一个出错不影响其他的
            failed += 1
            print(_paint(f"  [{n}/{len(ids)}] {document_id}：没补上（{err}）", _RED))
    print(_paint("完成", _GREEN) if not failed else _paint(f"完成，{failed} 个没补上", _YELLOW))
    print('刷新审核页面，就能用"原文文字"和框选了。')
    return 0 if not failed else 1


def _worker(stage: str = "all") -> int:
    from jacara.worker import run_worker

    names = {"all": "后台任务", "read": "后台任务（OCR 工位）", "extract": "后台任务（模型工位）"}
    print(f"{names[stage]}已启动，等待上传的文件（Ctrl + C 停止）")
    run_worker(stage)  # type: ignore[arg-type]
    return 0


def _watch(args: argparse.Namespace) -> int:
    """监视文件夹：放进去的图片和 PDF 自动导入（ADR 0036）。不动、不删原文件。"""
    from sqlalchemy import select

    from jacara.core.config import get_settings
    from jacara.core.db import session_scope
    from jacara.core.owner import PERSONAL_OWNER
    from jacara.core.queue import CeleryQueue
    from jacara.core.storage import get_storage
    from jacara.models import User
    from jacara.modules.auth.service import owner_of
    from jacara.pipeline.segment import SplitError, normalize_split
    from jacara.pipeline.watch import Watcher

    settings = get_settings()
    folder = args.folder or settings.jacara_watch_dir
    if folder is None:
        print("要监视哪个文件夹？写在命令后面，或者在 .env 里写 JACARA_WATCH_DIR=文件夹路径")
        return 1
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        print(f"找不到文件夹：{folder}")
        return 1
    try:
        split = normalize_split(args.split)
    except SplitError as err:
        print(err)
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
    watcher = Watcher(
        folder,
        None,
        get_storage(),
        CeleryQueue(),
        owner,
        template=args.template or settings.jacara_watch_template,
        split=split,
    )
    print(f"监视文件夹：{folder}（放进去的图片和 PDF 会自动导入，不动原文件；Ctrl + C 停止）")
    from contextlib import suppress

    with suppress(KeyboardInterrupt):
        watcher.run()
    return 0


def _api(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"接口文档：http://127.0.0.1:{args.port}/docs（Ctrl + C 停止）")
    print("只启动了 API；日常使用请用 jacara start，开发网页请在 web/ 里 npm run dev")
    # 只监听本机。reload 模式要传"模块:变量"字符串，uvicorn 才能在改代码后重新导入。
    uvicorn.run("jacara.app:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


def _start(args: argparse.Namespace) -> int:
    """日常使用：一个命令启动全部，并打开浏览器。

    worker 和大模型是单独的进程，用 subprocess 在后台启动；API 在这个进程里运行。
    按 Ctrl + C（或在别的终端运行 jacara stop）：这里等它们都退出，不留下"孤儿进程"。
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

    # 先查自己写错的（模型文件不存在），再查端口：不管系统有没有在运行，结果都一样
    model = None if args.no_model else (args.model or settings.find_model())
    if model is not None and not model.exists():
        print(f"找不到模型文件：{model}")
        return 1

    url = f"http://127.0.0.1:{args.port}"
    if not _port_free(args.port):
        if _answers(f"{url}/api/v1/health"):
            # 已经开着了：再开一个没有意义，直接把网页打开
            print(_paint(f"JacarandaParse 已经在运行了：{url}", _GREEN))
            print("要重启的话，先运行 uv run jacara stop，再运行 uv run jacara start。")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        print(_paint(f"不能启动：端口 {args.port} 被别的程序占用了", _RED))
        print(f"运行 lsof -i :{args.port} 看是哪个程序（PID 那一列），再 kill 那个 PID。")
        return 1

    # 数据库和任务队列在 Docker 里：电脑重启后 Docker 没开，这里先说清楚，不要等打开网页才报一堆错
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
        # 没有模型文件、8080 上也没有模型服务：能启动，但上传的文件全都会处理失败
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
            print(err)
            return 1
        # 每次启动生成一个新密钥：本机别的程序（包括浏览器里打开的网页）不知道密钥就用不了模型。
        # 密钥放在环境变量里传给 llama-server 和 worker，不写在命令行上（ps 能看到命令行）
        key = _new_model_key()
        env = {**server_env(), "LLAMA_API_KEY": key}
        os.environ["MODEL_API_KEY"] = key  # worker 是这里启动的，会继承
        # 直接启动 llama-server，不经过 jacara serve：停止时 terminate 发给它本人，不会漏掉。
        children.append(subprocess.Popen(command, env=env))  # noqa: S603
        print(f"启动模型服务：{model.name} → http://127.0.0.1:{MODEL_PORT}")
        if mmproj is not None:
            print(f"视觉模型：{mmproj.name}（照片、扫描件 OCR 不可靠时让模型看原图）")
    worker = [sys.executable, "-m", "jacara.cli", "worker"]
    if settings.jacara_pipeline:
        # 流水线（ADR 0051）：OCR 工位和模型工位各一个进程，同时干活。
        # 上次中断的先在这里统一标成失败：等两个工位各自起来再做，会误伤另一个已经开始干的活
        _reset_interrupted()
        children.append(subprocess.Popen([*worker, "--stage", "read"]))  # noqa: S603
        children.append(subprocess.Popen([*worker, "--stage", "extract"]))  # noqa: S603
    else:
        children.append(subprocess.Popen(worker))  # noqa: S603
    if settings.jacara_watch_dir is not None:
        # 监视文件夹（ADR 0036）：.env 里写了 JACARA_WATCH_DIR 才启动
        children.append(subprocess.Popen([sys.executable, "-m", "jacara.cli", "watch"]))
        print(f"监视文件夹：{settings.jacara_watch_dir}")

    # 记下自己和子进程的进程号：jacara stop 先请这里停，万一这里已经没了，也能把子进程收拾掉
    RUN_FILE.parent.mkdir(exist_ok=True)
    run = {"main": os.getpid(), "children": [c.pid for c in children]}
    RUN_FILE.write_text(json.dumps(run), encoding="utf-8")
    # 收到 SIGTERM（kill、关机）也走 Ctrl + C 的路：uvicorn 停下后会把信号再发一次，
    # 默认处理是立刻结束进程，下面 finally 里停模型和 worker 的代码就跑不到，留下孤儿进程。
    previous = signal.signal(signal.SIGTERM, _interrupt)
    print(f"打开 {url}（Ctrl + C 或 uv run jacara stop 全部停止）")
    if not args.no_browser:
        # 等 Web 服务起来再开浏览器；模型可能还要十几秒才加载完，上传的文件会排队等它
        threading.Timer(2.0, webbrowser.open, [url]).start()
    try:
        uvicorn.run("jacara.app:app", host="127.0.0.1", port=args.port)
    except KeyboardInterrupt:
        pass  # Ctrl + C 或 jacara stop：正常停止
    finally:
        _stop(children)
        RUN_FILE.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, previous)
    return 0


def _reset_interrupted() -> None:
    from jacara.pipeline.process import reset_interrupted

    try:
        count = reset_interrupted()
    except Exception as err:  # 数据库还没起来：worker 处理时自然会报，这里不挡启动
        print(_paint(f"提醒：没能检查上次中断的文档（{err}）", _YELLOW))
        return
    if count:
        print(_paint(f"{count} 个文档上次处理到一半被中断，已标记为失败，可以重新处理", _YELLOW))


def _new_model_key() -> str:
    """生成模型服务的密钥，写进 .run/model-key（只有自己能读），命令行抽取也从这里读。"""
    import secrets

    from jacara.core.config import MODEL_KEY_FILE

    key = secrets.token_urlsafe(32)
    MODEL_KEY_FILE.parent.mkdir(exist_ok=True)
    MODEL_KEY_FILE.touch(mode=0o600, exist_ok=True)
    MODEL_KEY_FILE.chmod(0o600)
    MODEL_KEY_FILE.write_text(key, encoding="utf-8")
    return key


def _interrupt(signum: int, frame: object) -> None:
    """把 SIGTERM 当成 Ctrl + C。"""
    raise KeyboardInterrupt


def _running() -> bool:
    """jacara start 是不是正在运行。"""
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
    from jacara.core.storage import S3Storage

    return commands.run_backup(get_engine(), S3Storage(settings), args.out, __version__, _running())


def _stop_running() -> int:
    """jacara stop：请 jacara start 停下（它会把模型和 worker 一起停掉），再检查有没有剩下的子进程。

    发 SIGINT（和按 Ctrl + C 一样），start 会按正常流程收尾。
    """
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
        for _ in range(60):  # 最多等 30 秒：worker 可能正在处理一个文件
            if not _alive(main):
                break
            time.sleep(0.5)
        else:
            print(_paint(f"30 秒还没停下来，可以运行 kill -9 {main}", _YELLOW))
            return 1

    # start 正常退出时会停掉子进程；万一它是被强制结束的，子进程还在，这里补上
    left = [pid for pid in children if _alive(pid)]
    for pid in left:
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not any(_alive(pid) for pid in left):
            break
        time.sleep(0.5)
    for pid in left:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
    RUN_FILE.unlink(missing_ok=True)
    print(_paint("已经全部停止", _GREEN))
    return 0


def _alive(pid: int) -> bool:
    """这个进程还在不在。发 0 号信号只检查、不打扰它。"""
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 在，但不是我们的进程
    return True


def _answers(url: str, *, loading_ok: bool = False) -> bool:
    """这个地址能不能正常回答（用来判断占着端口的是不是我们自己的服务）。

    loading_ok：llama-server 加载模型时 /health 回答 503，也算"是模型服务"。
    trust_env=False：本机地址不走代理（系统设了代理时，httpx 默认会把请求交给代理）。
    """
    import httpx

    try:
        status = httpx.get(url, timeout=3, trust_env=False).status_code
    except httpx.HTTPError:
        return False
    return status == 200 or (loading_ok and status == 503)


def _wait_free(port: int, seconds: float = 10.0) -> bool:
    """等端口空出来：刚按了 Ctrl + C 时，上一次的模型可能还在退出，占着端口几秒钟。"""
    import time

    deadline = time.monotonic() + seconds
    while True:
        if _port_free(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _port_free(port: int) -> bool:
    """本机这个端口现在有没有人用。先查好，免得 worker 和模型都启动了，API 才发现端口被占。

    SO_REUSEADDR：刚停掉的服务会让端口在 TIME_WAIT 状态留一两分钟（lsof 看不到任何程序）。
    这时 llama-server 和 uvicorn 其实都能正常启动（它们也开了这个选项），
    只有真的有程序在监听才算占用。
    """
    import socket

    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _stop(children: list[subprocess.Popen[bytes]]) -> None:
    """让后台的进程都停下来：先礼貌地请它停（terminate），10 秒还没停就强制结束（kill）。"""
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
"""jacara start 启动的大模型用这个端口，和 .env 里 MODEL_SERVER 的默认值一样。"""


RUN_FILE = Path(__file__).resolve().parents[3] / ".run" / "jacara.json"
"""jacara start 运行时把进程号写在这里（.run/ 在 .gitignore 里），jacara stop 靠它找到进程。"""

_RED, _YELLOW, _GREEN = "31", "33", "32"


def _paint(text: str, color: str) -> str:
    """在终端里给文字上色（ANSI 颜色码）。输出到文件时不上色。"""
    if not color or not sys.stdout.isatty():
        return text
    return f"\033[{color}m{text}\033[0m"


if __name__ == "__main__":
    raise SystemExit(main())
