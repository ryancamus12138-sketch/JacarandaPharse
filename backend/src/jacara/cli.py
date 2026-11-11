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

    from jacara.plugins.exporters import DocumentReport


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
    p_serve.add_argument("--context", type=int, default=32768, help="上下文长度，默认 32768")

    p_ext = sub.add_parser("extract", help="识别图片或 PDF 并抽取字段（要先运行 jacara serve）")
    p_ext.add_argument("image", type=Path, help="图片或 PDF 路径")
    p_ext.add_argument("--server", default="http://127.0.0.1:8080", help="模型服务地址")
    p_ext.add_argument("--show-prompt", action="store_true", help="同时打印发给模型的内容")
    p_ext.add_argument(
        "--template",
        default="receipt",
        help="用哪个模板：receipt 常用 4 个字段（默认），receipt_full 收据上所有单个字段",
    )
    p_ext.add_argument("--excel", type=Path, help="同时把结果导出成 Excel，例如 results/收据.xlsx")
    p_ext.add_argument("--save", action="store_true", help="同时把结果存进数据库")

    sub.add_parser("templates", help="列出所有模板（backend/src/jacara/templates/*.toml）")

    sub.add_parser("doctor", help="检查数据库、任务队列、对象存储、本地模型是否连得上")

    p_api = sub.add_parser("api", help="启动 Web 服务（API 和接口文档）")
    p_api.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    p_api.add_argument("--reload", action="store_true", help="改了代码自动重启（开发用）")

    sub.add_parser("worker", help="启动后台任务：处理通过 API 上传的文件（要先运行 jacara serve）")

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
        choices=["document:create", "document:process", "value:review", "document:review"],
        help="给使用者开通的权限，可以写多次",
    )
    p_add.add_argument("--name", default="", help="显示名，例如 张三")
    p_pw = user_sub.add_parser("password", help="重设密码，并让这个用户已登录的全部失效")
    p_pw.add_argument("username")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    if args.command == "templates":
        return _templates()
    if args.command == "api":
        return _api(args)
    if args.command == "worker":
        return _worker()
    if args.command == "start":
        return _start(args)
    if args.command == "stop":
        return _stop_running()
    if args.command == "backfill-ocr":
        return _backfill_ocr()
    if args.command == "user":
        return _user(args)
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

    backend = create_model_backend(ModelConfig(base_url=args.server))
    pipeline = Pipeline(ocr=create_ocr_engine(), extractor=LlmExtractor(backend))
    try:
        pages = pipeline.read(path)
    except UnsupportedFileError as err:
        print(err)
        return 1

    reports = []
    for number, page in enumerate(pages, start=1):
        if len(pages) > 1:
            print(f"\n===== 第 {number} / {len(pages)} 页 =====")
        how = page.models.get("source", "OCR 识别")
        print(f"读取：{how} · {len(page.lines)} 块文字 · 用时 {page.duration_ms} 毫秒")
        if args.show_prompt:
            for message in build_messages(page, template.specs):
                print(f"\n----- {message.role} -----\n{message.content}")
            print("-----\n")
        source = page_label(path.name, number, len(pages))
        try:
            report = pipeline.extract_page(page, source, number, template.name)
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


def _eval(args: argparse.Namespace) -> int:
    import subprocess
    from datetime import datetime

    from jacara.eval.sroie import download_sroie, load_sroie

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

    from jacara.eval.run import run_eval, write_result
    from jacara.pipeline.run import Pipeline
    from jacara.plugins.extractors import LlmExtractor
    from jacara.plugins.model_backends import ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine

    backend = create_model_backend(ModelConfig(base_url=args.server))
    pipeline = Pipeline(ocr=create_ocr_engine(), extractor=LlmExtractor(backend))

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


def _worker() -> int:
    from jacara.worker import run_worker

    print("后台任务已启动，等待上传的文件（Ctrl + C 停止）")
    run_worker()
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

    children: list[subprocess.Popen[bytes]] = []
    if model is not None:
        from jacara.plugins.model_backends.server import server_command, server_env

        try:
            command = server_command(model, port=MODEL_PORT)
        except FileNotFoundError as err:
            print(err)
            return 1
        # 直接启动 llama-server，不经过 jacara serve：停止时 terminate 发给它本人，不会漏掉。
        children.append(subprocess.Popen(command, env=server_env()))  # noqa: S603
        print(f"启动模型服务：{model.name} → http://127.0.0.1:{MODEL_PORT}")
    children.append(subprocess.Popen([sys.executable, "-m", "jacara.cli", "worker"]))

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


def _interrupt(signum: int, frame: object) -> None:
    """把 SIGTERM 当成 Ctrl + C。"""
    raise KeyboardInterrupt


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
