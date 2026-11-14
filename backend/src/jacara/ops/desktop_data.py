"""The data folder of the desktop app: full and automatic backups, restoring them, and moving the
data folder to another place.

Layout of the data folder (data_home()):
  jacara.db, queue.db      database and task queue
  files/                   uploaded originals and everything else in the file store
  models/                  model files and selected.json (the models in use)
  data/settings.json       values changed on the settings page
  backups/                 full backups (jacara-<time>.tar)
  backups/auto/            automatic database backups (jacara-<time>.db)
  before-restore-<time>/   what a restore set aside; never deleted by the program
  logs/, .run/             logs and process records

Moving the data folder: the folder the app passes in JACARA_HOME stays the fixed starting point and
holds data-location.json, which names the folder in use (the approach of apps that keep a library
elsewhere and remember where it is). The launcher reads it at start."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from jacara.core.i18n import N_, stored, tr
from jacara.ops.backup import BackupError, unique_name

if TYPE_CHECKING:
    from jacara.core.maintenance import Job

LOCATION_FILE = "data-location.json"
"""In the starting folder: {"path": "<data folder in use>"}. Absent: the starting folder is used."""

DEFAULT_HOME = "JACARA_DEFAULT_HOME"
"""Environment variable holding the starting folder once the launcher has switched JACARA_HOME to
the data folder in use."""

BACKUPS = "backups"
AUTO = "auto"
ASIDE = "before-restore-"
MOVED_FOLDER = "JacarandaParse"
"""Name of the data folder created inside the folder the user chooses when moving."""

KEEP_IN_PLACE = frozenset({LOCATION_FILE, ".run"})
"""Never moved: the location record belongs to the starting folder; process records are rewritten
at the next start."""

DATABASE_FILES = ("jacara.db", "jacara.db-wal", "jacara.db-shm")
QUEUE_FILES = ("queue.db", "queue.db-wal", "queue.db-shm")
SETTINGS_FILE = Path("data") / "settings.json"
CHOICES_FILE = Path("models") / "selected.json"

SPACE_MARGIN = 1.05
"""Free space required at the destination of a move, as a multiple of the data size."""


class DataError(Exception):
    """An operation on the data folder cannot proceed; the message is for the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


Progress = Callable[[str], None]


# ---- Where the data is ----


def data_home() -> Path:
    from jacara.core.config import data_home as home

    return home()


def default_home() -> Path:
    """The starting folder the app passes in (JACARA_HOME before any move)."""
    return Path(os.environ.get(DEFAULT_HOME) or os.environ.get("JACARA_HOME") or data_home())


def recorded_location(start: Path) -> Path | None:
    """The data folder named in the starting folder's data-location.json, if any."""
    try:
        raw = json.loads((start / LOCATION_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    value = raw.get("path") if isinstance(raw, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def holds_data(folder: Path) -> bool:
    """The folder holds a JacarandaParse data folder (its database). A drive mounted at the same
    place without the data (another disk given the same letter, an empty folder) does not count:
    an empty data folder must never be started in place of the moved one."""
    return (folder / DATABASE_FILES[0]).is_file()


def record_location(start: Path, target: Path | None) -> None:
    """Remember target as the data folder; None goes back to the starting folder."""
    path = start / LOCATION_FILE
    if target is None or target == start:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"path": str(target)}, ensure_ascii=False), "utf-8")
    os.replace(temporary, path)


def folder_size(folder: Path, skip: frozenset[str] = frozenset()) -> int:
    total = 0
    for path in _files(folder, skip):
        with contextlib.suppress(OSError):
            total += path.stat().st_size
    return total


def _folders(folder: Path, skip: frozenset[str] = frozenset()) -> list[Path]:
    """Every folder under folder, except top-level entries named in skip (empty ones included:
    moving keeps the layout)."""
    found: list[Path] = []
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if entry.name not in skip and entry.is_dir() and not entry.is_symlink():
            found.append(entry)
            found += sorted(p for p in entry.rglob("*") if p.is_dir() and not p.is_symlink())
    return found


def _files(folder: Path, skip: frozenset[str] = frozenset()) -> Iterator[Path]:
    """Every file under folder (links are copied as the files they point to), except top-level
    entries named in skip."""
    for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
        if entry.name in skip:
            continue
        if entry.is_dir():
            yield from (p for p in sorted(entry.rglob("*")) if p.is_file())
        elif entry.is_file():
            yield entry


# ---- Backups ----


@dataclass
class BackupFile:
    name: str
    path: str
    kind: Literal["full", "database"]
    """full: database, files and settings (.tar); database: automatic database backup (.db)."""
    size: int
    created: float
    models: bool = False
    """A full backup that includes model files."""


def backups_folder() -> Path:
    return data_home() / BACKUPS


def list_backups() -> list[BackupFile]:
    """Backups in the data folder, newest first."""
    from jacara.ops.backup import read_manifest

    found: list[BackupFile] = []
    for path in backups_folder().glob("jacara-*.tar"):
        try:
            models = any(n.startswith("models/") for n in read_manifest(path).extras)
        except (BackupError, OSError, tarfile.TarError, KeyError):
            continue  # Not a complete backup (for example still being written elsewhere).
        stat = path.stat()
        found.append(BackupFile(path.name, str(path), "full", stat.st_size, stat.st_mtime, models))
    for path in (backups_folder() / AUTO).glob("jacara-*.db"):
        stat = path.stat()
        found.append(BackupFile(path.name, str(path), "database", stat.st_size, stat.st_mtime))
    return sorted(found, key=lambda b: b.created, reverse=True)


def models_in_use() -> list[tuple[str, Path]]:
    """Model files the settings use, with their place under models/ (as stored in a backup).
    Files outside models/ (the table model shipped with the program, bundled OCR models) are not
    part of the data and are left out."""
    from jacara.core.config import get_settings

    settings = get_settings()
    models = data_home() / "models"
    found = [
        settings.find_model(),
        settings.find_mmproj(),
        settings.table_model(),
        *settings.ocr_models().values(),
    ]
    used: list[tuple[str, Path]] = []
    for path in found:
        if path is None:
            continue
        try:
            relative = path.absolute().relative_to(models.absolute()).as_posix()
        except ValueError:
            continue
        if path.is_file() and relative not in {r for r, _ in used}:
            used.append((relative, path))
    return used


def _sha256(path: Path, read: Callable[[int], None] = lambda _n: None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
            read(len(block))
    return digest.hexdigest()


def full_backup(job: Job, destination: Path | None, with_models: bool) -> dict[str, Any]:
    """Everything needed to continue on another computer: the database, every stored file, the
    settings, the list of models in use (name and SHA-256) and, when asked, the model files.
    The background services are paused meanwhile and changes are refused (see maintenance)."""
    from jacara import __version__
    from jacara.core.db import get_engine
    from jacara.core.storage import get_storage
    from jacara.ops.backup import backup, check_archive

    folder = destination or backups_folder()
    folder.mkdir(parents=True, exist_ok=True)
    if not os.access(folder, os.W_OK):
        raise DataError(tr("无法写入这个文件夹：{folder}", folder=folder))
    home = data_home()
    extras: list[tuple[str, Path]] = [
        (name, home / relative)
        for name, relative in (("settings.json", SETTINGS_FILE), ("selected.json", CHOICES_FILE))
        if (home / relative).is_file()
    ]
    used = models_in_use()
    listing = folder / ".jacara-models.json"
    job.report(stored("正在记录使用中的模型"))
    rows = [
        {"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)}
        for relative, path in used
    ]
    listing.write_text(json.dumps(rows, ensure_ascii=False, indent=2), "utf-8")
    extras.append(("models.json", listing))
    if with_models:
        extras += [(f"models/{relative}", path) for relative, path in used]
    try:
        path = backup(
            get_engine(),
            get_storage(),
            folder,
            __version__,
            lambda message: job.report(stored("正在备份：{detail}", detail=message)),
            extras,
        )
    finally:
        listing.unlink(missing_ok=True)
    job.report(stored("正在核对备份包"))
    manifest = check_archive(path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)  # Business data: readable by the owner only.
    return {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "documents": manifest.tables.get("documents", 0),
        "files": len(manifest.objects),
        "models": with_models,
    }


def auto_backup(keep: int) -> Path:
    """Automatic database backup into backups/auto/, keeping the newest keep (Immich's defaults:
    daily, 14 kept). SQLite's online backup copies a consistent database while the app runs."""
    source = data_home() / "jacara.db"
    folder = backups_folder() / AUTO
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_name(folder, "jacara-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"), ".db")
    partial = target.with_name(target.name + ".partial")
    with (
        contextlib.closing(sqlite3.connect(source, timeout=30)) as src,
        contextlib.closing(sqlite3.connect(partial)) as dst,
    ):
        src.backup(dst)
    os.replace(partial, target)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)
    newest_first = sorted(folder.glob("jacara-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in newest_first[max(keep, 1) :]:
        old.unlink(missing_ok=True)
    return target


def last_auto_backup() -> float | None:
    times = [p.stat().st_mtime for p in (backups_folder() / AUTO).glob("jacara-*.db")]
    return max(times) if times else None


# ---- Restore ----


def _revision_of(database: Path) -> str | None:
    with contextlib.closing(sqlite3.connect(database)) as conn:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.DatabaseError:
            return None
    return row[0] if row else None


def _known_revision(revision: str | None) -> bool:
    from alembic.script import ScriptDirectory

    from jacara.core.migrate import alembic_config

    if revision is None:
        return True
    return ScriptDirectory.from_config(alembic_config()).get_revision(revision) is not None


FILE_CHECKS = frozenset(
    {
        N_("原始文件（原图、PDF）"),
        N_("OCR 样本小图"),
        N_("OCR 快照"),
        N_("导出记录的文件"),
        N_("头像"),
    }
)
"""Verification checks about stored files. After restoring only the database, files deleted since
that backup are missing; that is reported, not treated as a failed restore."""


def restore_backup(job: Job, archive: Path) -> dict[str, Any]:
    """Restore a backup into the data folder. What is there now is first moved aside into
    before-restore-<time>/ (never deleted); the backup is restored into the emptied place and every
    item is opened; on any failure the new data is removed and the old data moved back.

    Full backup (.tar): database, files, settings and the model files it contains. Database backup
    (.db): only the database; the files stay."""
    from sqlalchemy.orm import sessionmaker

    from jacara.core.config import get_settings
    from jacara.core.db import create_db_engine
    from jacara.core.storage import LOCAL_FILES, LocalStorage
    from jacara.ops.backup import check_archive, restore
    from jacara.ops.commands import migrate_to
    from jacara.ops.verify import verify

    full = archive.suffix == ".tar"
    if not archive.is_file() or archive.suffix not in (".tar", ".db"):
        raise DataError(tr("找不到备份文件：{archive}", archive=archive))
    if full:
        job.report(stored("正在核对备份包"))
        manifest = check_archive(archive)
        if not _known_revision(manifest.revision):
            raise DataError(tr("这个备份由更新的版本创建，请先安装更新的版本"))
    else:
        revision = _revision_of(archive)
        if revision is None:
            raise DataError(tr("这个文件不是 JacarandaParse 的数据库备份"))
        if not _known_revision(revision):
            raise DataError(tr("这个备份由更新的版本创建，请先安装更新的版本"))
    home = data_home()
    url = get_settings().database_url
    aside = unique_name(home, ASIDE + datetime.now(UTC).strftime("%Y%m%d-%H%M%S"), "")
    names = [*DATABASE_FILES, *QUEUE_FILES]
    if full:
        names += [LOCAL_FILES, str(SETTINGS_FILE), str(CHOICES_FILE)]
    moved: list[str] = []
    created: list[Path] = []
    job.report(stored("正在把现有数据移到 {folder}", folder=aside.name))
    aside.mkdir()
    for name in names:
        if (home / name).exists():
            (aside / name).parent.mkdir(parents=True, exist_ok=True)
            os.replace(home / name, aside / name)
            moved.append(name)
    warnings: list[str] = []
    try:
        if full:
            engine = create_db_engine(url)
            storage = LocalStorage(home / LOCAL_FILES)
            created += [home / n for n in (*DATABASE_FILES, LOCAL_FILES)]
            try:
                restore(
                    archive,
                    engine,
                    storage,
                    lambda rev: migrate_to(url, rev),
                    lambda message: job.report(stored("正在恢复：{detail}", detail=message)),
                )
                migrate_to(url, None)
                created += _restore_extras(job, archive, home)
                job.report(stored("正在逐项核对恢复的数据"))
                checks = verify(sessionmaker(bind=engine, expire_on_commit=False), storage)
            finally:
                engine.dispose()
            failed = [c for c in checks if not c.passed]
            if failed:
                raise DataError(
                    tr("恢复后核对未通过：{detail}", detail=tr("；").join(_lines(failed)))
                )
        else:
            job.report(stored("正在恢复数据库"))
            shutil.copyfile(archive, home / "jacara.db")
            created += [home / n for n in DATABASE_FILES]
            migrate_to(url, None)
            job.report(stored("正在逐项核对恢复的数据"))
            engine = create_db_engine(url)
            try:
                checks = verify(
                    sessionmaker(bind=engine, expire_on_commit=False),
                    LocalStorage(home / LOCAL_FILES),
                )
            finally:
                engine.dispose()
            broken = [c for c in checks if not c.passed and c.name not in FILE_CHECKS]
            if broken:
                raise DataError(
                    tr("恢复后核对未通过：{detail}", detail=tr("；").join(_lines(broken)))
                )
            warnings = _lines([c for c in checks if not c.passed])
    except BaseException:
        job.report(stored("恢复失败，正在放回原来的数据"))
        for path in created:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for name in moved:
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            os.replace(aside / name, home / name)
        shutil.rmtree(aside, ignore_errors=True)
        raise
    return {"aside": str(aside), "kind": "full" if full else "database", "warnings": warnings}


def _lines(checks: list[Any]) -> list[str]:
    return [
        tr(
            "{name}：{failed} 个打不开（{first}）",
            name=tr(c.name),
            failed=len(c.failed),
            first=c.failed[0],
        )
        for c in checks
    ]


def _restore_extras(job: Job, archive: Path, home: Path) -> list[Path]:
    """Settings and model files from a full backup. A model file already present with the same
    content is kept as it is."""
    from jacara.ops.backup import read_manifest

    extras = read_manifest(archive).extras
    created: list[Path] = []
    targets = {"settings.json": home / SETTINGS_FILE, "selected.json": home / CHOICES_FILE}
    with tarfile.open(archive, "r") as tar:
        for name, info in extras.items():
            if name in targets:
                target = targets[name]
            elif name.startswith("models/") and ".." not in Path(name).parts:
                target = home / name
                if (
                    target.is_file()
                    and target.stat().st_size == info["size"]
                    and _sha256(target) == info["sha256"]
                ):
                    continue
            else:
                continue
            member = tar.extractfile(f"extra/{name}")
            if member is None:
                continue
            job.report(stored("正在恢复 {name}", name=name))
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            with partial.open("wb") as out:
                shutil.copyfileobj(member, out, 1 << 20)
            os.replace(partial, target)
            created.append(target)
    return created


def models_missing(archive: Path) -> list[str]:
    """Models in use when a full backup was made that are neither in it nor in the data folder:
    to be downloaded again."""
    from jacara.ops.backup import read_manifest

    manifest = read_manifest(archive)
    if "models.json" not in manifest.extras:
        return []
    with tarfile.open(archive, "r") as tar:
        member = tar.extractfile("extra/models.json")
        rows = json.loads(member.read()) if member is not None else []
    home = data_home()
    return [
        row["path"]
        for row in rows
        if f"models/{row['path']}" not in manifest.extras
        and not (home / "models" / row["path"]).is_file()
    ]


# ---- Moving the data folder ----


def move_target(parent: Path | None) -> Path:
    """Where the data goes: a JacarandaParse folder inside the chosen folder, or the starting folder
    (None)."""
    return default_home() if parent is None else parent / MOVED_FOLDER


def check_move(parent: Path | None) -> dict[str, Any]:
    """Check a move before starting it: the destination exists and is writable, is not the current
    folder or inside it, is empty, and has room. Returns what the page shows before confirming."""
    source = data_home().resolve()
    if parent is not None:
        if not parent.is_absolute() or not parent.is_dir():
            raise DataError(tr("请选择一个已存在的文件夹"))
        if not os.access(parent, os.W_OK):
            raise DataError(tr("无法写入这个文件夹：{folder}", folder=parent))
    target = move_target(parent)
    resolved = target.resolve()
    if resolved == source:
        raise DataError(tr("数据已经在这个位置"))
    if resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise DataError(tr("不能移到当前数据文件夹里面，也不能移到包含它的文件夹"))
    if target.exists() and any(p.name not in KEEP_IN_PLACE | {"logs"} for p in target.iterdir()):
        raise DataError(tr("目标位置已经有内容：{folder}", folder=target))
    size = folder_size(data_home(), KEEP_IN_PLACE)
    free = shutil.disk_usage(target if target.exists() else target.parent).free
    if free < size * SPACE_MARGIN:
        raise DataError(
            tr(
                "目标磁盘空间不足：需要 {need}，可用 {free}",
                need=_size(size * SPACE_MARGIN),
                free=_size(free),
            )
        )
    return {"target": str(target), "size": size, "free": free}


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def move_data(job: Job, parent: Path | None) -> dict[str, Any]:
    """Copy the data folder to the new place, check every copied file against its SHA-256, then
    record the new place and remove the old copy. The background services must be stopped and the
    database closed. Nothing is removed until every file has been checked; on failure the partial
    copy is removed and the data stays where it was."""
    check = check_move(parent)
    source = data_home()
    target = Path(check["target"])
    total = int(check["size"])
    files = list(_files(source, KEEP_IN_PLACE))
    folders = _folders(source, KEEP_IN_PLACE)
    with (
        contextlib.suppress(sqlite3.Error),
        contextlib.closing(sqlite3.connect(source / "jacara.db")) as conn,
    ):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    created_root = not target.exists()
    copied: list[Path] = []
    done = 0
    try:
        target.mkdir(parents=True, exist_ok=True)
        for folder in folders:
            (target / folder.relative_to(source)).mkdir(parents=True, exist_ok=True)
        for path in files:
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with path.open("rb") as src, destination.open("wb") as dst:
                for block in iter(lambda f=src: f.read(1 << 20), b""):
                    digest.update(block)
                    dst.write(block)
                    done += len(block)
                    job.report(stored("正在复制 {name}", name=relative.as_posix()), done, total)
            shutil.copystat(path, destination)
            copied.append(destination)
            job.report(stored("正在核对 {name}", name=relative.as_posix()), done, total)
            if _sha256(destination) != digest.hexdigest():
                raise DataError(tr("复制后核对不一致：{name}", name=relative.as_posix()))
    except BaseException:
        for path in copied:
            path.unlink(missing_ok=True)
        if created_root:
            shutil.rmtree(target, ignore_errors=True)
        raise
    start = default_home()
    record_location(start, target)
    os.environ["JACARA_HOME"] = str(target)
    job.report(stored("正在清理原来的位置"), total, total)
    for path in files:
        path.unlink(missing_ok=True)
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            folder.rmdir()  # Only empty folders; anything left unexpected stays.
    if source != start:
        # A folder chosen earlier: nothing of the app is left there.
        shutil.rmtree(source / ".run", ignore_errors=True)
        with contextlib.suppress(OSError):
            source.rmdir()
    return {"path": str(target), "size": total}
