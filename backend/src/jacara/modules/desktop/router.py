"""Desktop app integration: /api/v1/desktop. Only in the desktop app (JACARA_DESKTOP=true);
elsewhere every endpoint answers 404.

- Downloads, revealing files and opening links: see core/desktop_files.py.
- The data folder: backups, restoring, moving the folder, restarting
  the background services. These run as maintenance jobs (core/maintenance.py) that the page
  follows through GET /desktop/job.
- System dialogs for choosing a folder or a backup file: a page cannot learn a folder's path."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from jacara.core.config import get_settings
from jacara.core.deps import get_owner
from jacara.core.desktop_files import (
    DesktopFileError,
    choose_file,
    choose_folder,
    open_folder,
    open_url,
    reveal,
    save_download,
)
from jacara.core.i18n import tr
from jacara.core.maintenance import MAINTENANCE, Job, JobBusy
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.downloads.manager import DOWNLOADS
from jacara.ops.desktop_data import (
    DataError,
    backups_folder,
    check_move,
    data_home,
    default_home,
    folder_size,
    holds_data,
    last_auto_backup,
    list_backups,
    models_missing,
    record_location,
)


def desktop_app() -> None:
    if not get_settings().jacara_desktop:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


def desktop_only(owner: Annotated[Owner, Depends(get_owner)]) -> Owner:
    desktop_app()
    return owner


router = APIRouter(prefix="/desktop", tags=["桌面版"], dependencies=[Depends(desktop_only)])
location_router = APIRouter(
    prefix="/desktop/location", tags=["桌面版"], dependencies=[Depends(desktop_app)]
)
"""Answers while the data folder cannot be found: no database is opened for these."""

Manager = Annotated[Owner, Depends(desktop_only)]


class SavedFile(BaseModel):
    name: str
    """File name in the Downloads folder (may have " (1)" added)."""
    path: str


class RevealIn(BaseModel):
    path: str


class OpenUrlIn(BaseModel):
    url: str


class BackupOut(BaseModel):
    name: str
    path: str
    kind: Literal["full", "database"]
    size: int
    created: float
    models: bool


class DataOut(BaseModel):
    path: str
    """Data folder in use."""
    default: str
    """The app's own place for the data."""
    size: int
    backups: list[BackupOut]
    last_auto_backup: float | None


class BackupIn(BaseModel):
    destination: str | None = None
    """Folder to save the backup in; None: backups/ in the data folder."""
    models: bool = False
    """Include the model files in use."""


class RestoreIn(BaseModel):
    path: str


class MoveIn(BaseModel):
    parent: str | None = None
    """Folder to move the data into (a JacarandaParse folder is created inside); None: back to the
    app's own place."""


class MoveCheck(BaseModel):
    target: str
    size: int
    free: int


class JobOut(BaseModel):
    kind: str
    running: bool
    stage: str
    done: int
    total: int
    started: float
    finished: float | None
    result: dict[str, Any] | None
    error: str | None


class ChooseIn(BaseModel):
    purpose: Literal["watch", "backup", "move"]


class ChosenOut(BaseModel):
    path: str | None
    """None when the dialog was cancelled."""


class LocationOut(BaseModel):
    unavailable: str | None
    """The data folder in use cannot be found (for example on a drive not connected)."""
    default: str


def _no_dialog() -> str:
    return tr("这台电脑无法打开系统的选择对话框，请直接输入完整路径")


def _job_out(job: Job) -> JobOut:
    return JobOut(**job.view())


def _start(kind: Any, block: Any, work: Any) -> JobOut:
    try:
        return _job_out(MAINTENANCE.start(kind, block, work))
    except JobBusy as err:
        raise HTTPException(status.HTTP_409_CONFLICT, tr("另一项操作正在进行，请稍候")) from err


def _bad(err: Exception) -> HTTPException:
    message = getattr(err, "message", None) or str(err)
    return HTTPException(status.HTTP_400_BAD_REQUEST, message)


def _settings_started() -> None:
    """The background services now use the saved settings (no restart reminder)."""
    from jacara.modules.app_settings import service

    service.STARTED = time.time()


# ---- Files and links ----


@router.post("/downloads", summary="把网页生成的文件存进下载文件夹")
def save_file(file: UploadFile, filename: Annotated[str, Form(max_length=255)]) -> SavedFile:
    """A file made in the page (for example a CSV) is saved to the Downloads folder, as a browser
    would. Files from the service are saved by sending X-Jacara-Save: downloads instead."""
    target = save_download(filename, file.file.read())
    return SavedFile(name=target.name, path=str(target))


@router.post("/reveal", status_code=status.HTTP_204_NO_CONTENT, summary="在访达或资源管理器中显示")
def show_file(data: RevealIn) -> None:
    """Show a saved download selected in Finder or File Explorer (files in Downloads only)."""
    try:
        reveal(Path(data.path))
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("找不到这个文件")) from err


@router.post("/open-url", status_code=status.HTTP_204_NO_CONTENT, summary="用默认浏览器打开网址")
def open_link(data: OpenUrlIn) -> None:
    """Open an http or https address in the default browser instead of the app window."""
    try:
        open_url(data.url)
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("只能打开 http 或 https 网址")) from err


@router.post(
    "/open-folder", status_code=status.HTTP_204_NO_CONTENT, summary="打开数据、备份或日志文件夹"
)
def open_data_folder(which: Literal["data", "backups", "logs"]) -> None:
    from jacara.desktop import log_dir

    folder = {"data": data_home, "backups": backups_folder, "logs": log_dir}[which]()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        open_folder(folder)
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("找不到这个文件夹")) from err


@router.post("/choose-folder", summary="用系统对话框选择文件夹")
def ask_folder(data: ChooseIn) -> ChosenOut:
    prompts = {
        "watch": tr("选择要监视的文件夹"),
        "backup": tr("选择备份保存位置"),
        "move": tr("选择数据文件夹的新位置"),
    }
    try:
        chosen = choose_folder(prompts[data.purpose])
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _no_dialog()) from err
    return ChosenOut(path=str(chosen) if chosen else None)


@router.post("/choose-file", summary="用系统对话框选择备份文件")
def ask_backup_file() -> ChosenOut:
    try:
        chosen = choose_file(tr("选择备份文件"), ("tar", "db"))
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _no_dialog()) from err
    return ChosenOut(path=str(chosen) if chosen else None)


# ---- Data folder ----


@router.get("/data", summary="数据文件夹和备份")
def data_info() -> DataOut:
    return DataOut(
        path=str(data_home()),
        default=str(default_home()),
        size=folder_size(data_home()),
        backups=[BackupOut(**b.__dict__) for b in list_backups()],
        last_auto_backup=last_auto_backup(),
    )


@router.get("/job", summary="当前或上一项数据操作")
def current_job() -> JobOut | None:
    job = MAINTENANCE.job
    return _job_out(job) if job is not None else None


@router.post("/backups", status_code=status.HTTP_202_ACCEPTED, summary="立即备份")
def make_backup(data: BackupIn, owner: Manager) -> JobOut:
    """Full backup: database, files, settings, the list of models in use and, when asked, the
    model files. Background work pauses and changes wait until it is done."""
    from jacara.desktop import services
    from jacara.ops.desktop_data import full_backup

    require(owner, "system:manage")
    destination = Path(data.destination) if data.destination else None
    if destination is not None and (not destination.is_absolute() or not destination.is_dir()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("请选择一个已存在的文件夹"))

    def work(job: Job) -> dict[str, Any]:
        services().pause_workers()
        try:
            return full_backup(job, destination, data.models)
        finally:
            services().resume_workers()

    return _start("backup", "writes", work)


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED, summary="从备份恢复")
def restore_from(data: RestoreIn, owner: Manager) -> JobOut:
    """Restore a full or database backup. The data there now is moved aside into
    before-restore-<time>/ in the data folder (never deleted); on any failure it is moved back."""
    from jacara.core.runtime import reset_caches
    from jacara.desktop import services
    from jacara.ops.desktop_data import restore_backup
    from jacara.pipeline.process import reset_interrupted

    require(owner, "system:manage")
    archive = Path(data.path)
    if not archive.is_file() or archive.suffix not in (".tar", ".db"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("请选择 .tar 或 .db 备份文件"))

    def work(job: Job) -> dict[str, Any]:
        # Model downloads write into the data folder: pause them (partial files are kept).
        DOWNLOADS.pause()
        services().stop()
        reset_caches()
        try:
            result = restore_backup(job, archive)
            if archive.suffix == ".tar":
                result["models_missing"] = models_missing(archive)
            return result
        finally:
            reset_caches()
            reset_interrupted()
            services().start()
            _settings_started()

    return _start("restore", "everything", work)


@router.post("/move/check", summary="检查数据文件夹能否移到这里")
def check_moving(data: MoveIn, owner: Manager) -> MoveCheck:
    require(owner, "system:manage")
    try:
        return MoveCheck(**check_move(Path(data.parent) if data.parent else None))
    except DataError as err:
        raise _bad(err) from err


@router.post("/move", status_code=status.HTTP_202_ACCEPTED, summary="移动数据文件夹")
def move_folder(data: MoveIn, owner: Manager) -> JobOut:
    """Copy the data folder (database, files, models, backups, settings) to the new place, check
    every file, then switch to it and remove the old copy."""
    from jacara.core.runtime import reset_caches
    from jacara.desktop import services
    from jacara.ops.desktop_data import move_data

    require(owner, "system:manage")
    parent = Path(data.parent) if data.parent else None
    try:
        check_move(parent)
    except DataError as err:
        raise _bad(err) from err

    def work(job: Job) -> dict[str, Any]:
        # Model downloads write into the data folder: pause them (partial files are kept).
        DOWNLOADS.pause()
        services().stop()
        reset_caches()
        try:
            return move_data(job, parent)
        finally:
            reset_caches()
            services().start()
            _settings_started()

    return _start("move", "everything", work)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED, summary="重新启动后台服务")
def restart_services(owner: Manager) -> JobOut:
    """Apply changed settings and models: the workers, the folder watcher and the model server
    start again; documents being processed continue afterwards."""
    from jacara.desktop import services

    require(owner, "system:manage")

    def work(job: Job) -> dict[str, Any]:
        del job
        services().restart()
        _settings_started()
        return {}

    return _start("restart", "writes", work)


# ---- Data folder not found at start ----


@location_router.get("", summary="数据文件夹是否可用")
def location() -> LocationOut:
    unavailable = MAINTENANCE.unavailable
    return LocationOut(
        unavailable=str(unavailable) if unavailable else None, default=str(default_home())
    )


@location_router.post("/retry", summary="重新查找数据文件夹")
def retry_location() -> LocationOut:
    """The drive is connected again: start on the recorded data folder."""
    from jacara.desktop import open_data

    folder = MAINTENANCE.unavailable
    if folder is not None:
        if not holds_data(folder):
            raise HTTPException(
                status.HTTP_409_CONFLICT, tr("仍然找不到数据文件夹：{folder}", folder=folder)
            )
        os.environ["JACARA_HOME"] = str(folder)
        MAINTENANCE.unavailable = None
        open_data()
    return location()


@location_router.post("/default", summary="改用默认位置")
def use_default_location() -> LocationOut:
    """Stop looking for the moved data folder and use the app's own place again (data that was
    never moved there is not there: this starts with what the default place holds)."""
    from jacara.desktop import open_data

    if MAINTENANCE.unavailable is not None:
        start = default_home()
        record_location(start, None)
        os.environ["JACARA_HOME"] = str(start)
        MAINTENANCE.unavailable = None
        open_data()
    return location()
