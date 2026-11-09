"""命令行入口：`jacara`。

uv run jacara --version
uv run jacara ocr 图片或PDF                      识别文字（PDF 有文字层时直接读取）
uv run jacara serve --model 模型.gguf            启动本地大模型服务
uv run jacara extract 图片或PDF                  识别、抽取字段并核对出处（要先启动模型服务）
uv run jacara doctor                             检查数据库、队列、对象存储、本地模型是否连得上
uv run jacara api                                启动 Web 服务，浏览器打开 http://127.0.0.1:8000/docs
uv run jacara worker                             启动后台任务，处理通过 API 上传的文件
uv run jacara user add 用户名 --role admin        新建用户（团队模式的第一个管理员用它建）
uv run jacara user add 用户名 --allow value:review    新建使用者，并开通"修改和确认字段"
uv run jacara user password 用户名                重设密码
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jacara import __version__

if TYPE_CHECKING:
    # 只给类型检查用，运行时不导入：jacara --version 不用加载 OCR 和模型相关的库。
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

    p_api = sub.add_parser("api", help="启动 Web 服务（API 和接口文档）")
    p_api.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    p_api.add_argument("--reload", action="store_true", help="改了代码自动重启（开发用）")

    sub.add_parser("worker", help="启动后台任务：处理通过 API 上传的文件（要先运行 jacara serve）")

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
    if args.command == "api":
        return _api(args)
    if args.command == "worker":
        return _worker()
    if args.command == "user":
        return _user(args)
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
    from jacara.plugins.extractors import PRESETS, ExtractionError, LlmExtractor
    from jacara.plugins.extractors.llm import build_messages
    from jacara.plugins.model_backends import ModelBackendError, ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine
    from jacara.plugins.ocr.loader import UnsupportedFileError

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
            for message in build_messages(page, PRESETS[args.fields]):
                print(f"\n----- {message.role} -----\n{message.content}")
            print("-----\n")
        source = page_label(path.name, number, len(pages))
        try:
            report = pipeline.extract_page(page, source, number, args.fields)
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


def _worker() -> int:
    from jacara.worker import run_worker

    print("后台任务已启动，等待上传的文件（Ctrl + C 停止）")
    run_worker()
    return 0


def _api(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"接口文档：http://127.0.0.1:{args.port}/docs（Ctrl + C 停止）")
    # 只监听本机。reload 模式要传"模块:变量"字符串，uvicorn 才能在改代码后重新导入。
    uvicorn.run("jacara.app:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


_RED, _YELLOW, _GREEN = "31", "33", "32"


def _paint(text: str, color: str) -> str:
    """在终端里给文字上色（ANSI 颜色码）。输出到文件时不上色。"""
    if not color or not sys.stdout.isatty():
        return text
    return f"\033[{color}m{text}\033[0m"


if __name__ == "__main__":
    raise SystemExit(main())
