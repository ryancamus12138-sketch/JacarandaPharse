"""命令行：jacara backup、jacara restore、jacara restore-drill（ADR 0054）。"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from jacara.ops.backup import BackupError, check_archive, restore
from jacara.ops.verify import Check, verify

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from jacara.core.config import Settings
    from jacara.core.storage import S3Storage

DRILL_PROJECT = "jacara-drill"
DRILL_POSTGRES_PORT = 55432
DRILL_S3_PORT = 58333


def _say(message: str) -> None:
    print("  " + message, flush=True)


def _ini() -> Path:
    ini = Path("alembic.ini")
    if not ini.is_file():
        raise BackupError("请在项目根目录（有 alembic.ini 的地方）运行")
    return ini


def migrate_to(url: str, revision: str | None) -> None:
    """把这个数据库的结构变成 revision 那一版（None 或 head 就是最新）。只在表都是空的时候调用。"""
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    from sqlalchemy import create_engine

    config = Config(str(_ini()))
    config.attributes["url"] = url
    target = revision or "head"
    script = ScriptDirectory.from_config(config)
    if target != "head" and script.get_revision(target) is None:
        raise BackupError(f"备份是更新的代码做的（数据库版本 {target}），先把代码更新到那个版本")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    wanted = script.get_current_head() if target == "head" else target
    if current == wanted:
        return
    try:
        command.upgrade(config, target)
    except CommandError:
        command.downgrade(config, target)  # 空的库比备份新：退回备份时的版本，表是空的不丢东西


def _report(checks: list[Check]) -> bool:
    for c in checks:
        print(("  ✓ " if c.passed else "  ✗ ") + c.line())
    return all(c.passed for c in checks)


def run_backup(
    engine: Engine, storage: S3Storage, out_dir: Path, version: str, running: bool
) -> int:
    from sqlalchemy import func, select

    from jacara.models import Document
    from jacara.ops.backup import backup

    if running:
        print("jacara 正在运行。先 jacara stop，暂停写入以后再备份（数据库和文件才对得上）。")
        return 1
    with engine.connect() as conn:
        pending = conn.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.status.in_(("uploaded", "processing")))
        ).scalar()
    print(f"备份到 {out_dir}/（数据库每张表 + 对象存储里每个文件）")
    path = backup(engine, storage, out_dir, version, _say)
    os.chmod(path, 0o600)  # 里面是业务数据：只有自己能读
    manifest = check_archive(path)
    rows = sum(manifest.tables.values())
    size = path.stat().st_size / 1024 / 1024
    print(f"完成：{path}（{size:.1f} MB，{rows} 行，{len(manifest.objects)} 个文件，已核对）")
    if pending:
        print(f"注意：有 {pending} 份文档还在排队或处理中，恢复后要在列表里重新处理")
    print("建议：jacara restore-drill 这个包，在空环境里实际恢复一次看看")
    return 0


def run_restore(archive: Path, settings: Settings, running: bool) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from jacara.core.storage import S3Storage

    if running:
        print("jacara 正在运行。先 jacara stop 再恢复。")
        return 1
    url = settings.database_url
    engine = create_engine(url)
    storage = S3Storage(settings)
    try:
        print(f"恢复 {archive}（先核对整个包，再恢复到空的数据库和存储）")
        manifest = restore(archive, engine, storage, lambda rev: migrate_to(url, rev), _say)
        migrate_to(url, None)
        print(f"数据和 {len(manifest.objects)} 个文件都放回去了，逐样核对：")
        ok = _report(verify(sessionmaker(bind=engine, expire_on_commit=False), storage))
    except BackupError as err:
        print(err)
        return 1
    finally:
        engine.dispose()
    print("全部能打开。" if ok else "有打不开的，见上面。")
    return 0 if ok else 1


def _compose(args: list[str], env: dict[str, str]) -> None:
    subprocess.run(  # noqa: S603
        ["docker", "compose", "-p", DRILL_PROJECT, *args],  # noqa: S607
        check=True,
        env=env,
    )


def _wait(ready: Callable[[], bool], seconds: float = 90) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(2)
    return False


def run_drill(archive: Path, keep: bool) -> int:
    """恢复演练：另起一套空的数据库和对象存储（别的端口、别的数据卷），把包恢复进去、逐样打开，
    默认做完就删掉这套演练环境。平时用的数据库和存储完全不碰。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from jacara.core.config import Settings
    from jacara.core.storage import S3Storage

    try:
        _ini()
        manifest = check_archive(archive)
    except BackupError as err:
        print(err)
        return 1
    print(
        f"恢复演练：{archive}（{len(manifest.objects)} 个文件，{sum(manifest.tables.values())} 行）"
    )
    env = dict(os.environ)
    env.update(POSTGRES_PORT=str(DRILL_POSTGRES_PORT), S3_PORT=str(DRILL_S3_PORT))
    settings = Settings(
        postgres_port=DRILL_POSTGRES_PORT, s3_endpoint=f"http://127.0.0.1:{DRILL_S3_PORT}"
    )
    url = settings.database_url
    engine = create_engine(url)
    storage = S3Storage(settings)

    def db_ready() -> bool:
        try:
            with engine.connect() as conn:
                conn.execute(text("select 1"))
            storage.keys()
        except Exception:  # 还没起来
            return False
        return True

    ok = False
    try:
        ports = f"端口 {DRILL_POSTGRES_PORT}、{DRILL_S3_PORT}"
        _say(f"启动演练用的空环境（docker compose -p {DRILL_PROJECT}，{ports}）")
        _compose(["up", "-d", "postgres", "seaweedfs"], env)
        if not _wait(db_ready):
            print("演练环境 90 秒还没起来，看 docker compose -p jacara-drill logs")
            return 1
        restore(archive, engine, storage, lambda rev: migrate_to(url, rev), _say)
        migrate_to(url, None)
        print("逐样核对恢复出来的东西：")
        ok = _report(verify(sessionmaker(bind=engine, expire_on_commit=False), storage))
    except (BackupError, subprocess.CalledProcessError) as err:
        print(f"演练失败：{err}")
        return 1
    finally:
        engine.dispose()
        if keep:
            print(f"演练环境留着（--keep）：用完运行 docker compose -p {DRILL_PROJECT} down -v")
        else:
            _say("删掉演练环境（只删演练用的数据卷，平时的数据不动）")
            try:
                _compose(["down", "-v"], env)
            except subprocess.CalledProcessError:
                print(f"演练环境没删掉，手动运行 docker compose -p {DRILL_PROJECT} down -v")
    print("演练通过：这个备份包能完整恢复。" if ok else "演练没通过，见上面。")
    return 0 if ok else 1
