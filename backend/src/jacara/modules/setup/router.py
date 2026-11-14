"""First-start wizard of the desktop app: /api/v1/setup (ADR 0068 section 6). Only in the desktop
app; elsewhere every endpoint answers 404.

The wizard appears while no large model is found. It offers the models of models.lock to download
(the only network access of the program besides checking for updates, and only on a click), an
inbox folder for files downloaded elsewhere, and choosing a model file on this computer."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from jacara.core.desktop_files import DesktopFileError, choose_file, open_folder
from jacara.core.i18n import render, tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.downloads import SOURCES, DownloadError, inbox_dir, models_dir, read_state
from jacara.downloads.manager import DOWNLOADS
from jacara.modules.desktop.router import desktop_only

router = APIRouter(prefix="/setup", tags=["首次启动"], dependencies=[Depends(desktop_only)])

Manager = Annotated[Owner, Depends(desktop_only)]


class SourceOut(BaseModel):
    id: str
    label: str


class ListedOut(BaseModel):
    filename: str
    slot: Literal["model", "mmproj"]
    name: str
    size: int
    license: str
    default: bool
    in_library: bool
    """The model library already has this exact file."""


class SwitchOut(BaseModel):
    source: str
    label: str
    reason: str


class ItemOut(BaseModel):
    filename: str
    slot: str
    origin: Literal["download", "inbox", "file"]
    state: Literal["waiting", "downloading", "verifying", "importing", "done", "paused", "failed"]
    done: int
    total: int
    speed: int
    """Bytes per second over the last few seconds."""
    eta: int | None
    """Seconds left at the current speed."""
    source: str | None
    switched: list[SwitchOut]
    """Sources given up for this file, with the reason."""
    error: str | None
    imported: str | None
    custom: bool


class InboxFileOut(BaseModel):
    name: str
    size: int
    expected: int | None
    status: Literal[
        "copying",
        "waiting",
        "verifying",
        "importing",
        "in_library",
        "busy",
        "wrong_size",
        "failed",
        "unknown",
        "ignored",
    ]
    """copying: smaller than listed or still changing; waiting: complete, after the file being
    worked on; verifying: SHA-256 being checked; importing: being put into the model library;
    in_library: the library already has the listed file, so it is not imported again; busy: the same
    file is being downloaded or copied another way; wrong_size: larger than listed; failed: did not
    match the list; unknown: a .gguf file not in the list (import it with "Choose a file"); ignored:
    not a model file."""
    done: int | None
    total: int | None
    """Progress (bytes) while verifying or importing."""


class SetupOut(BaseModel):
    needs_model: bool
    """No large model is found: the wizard is shown."""
    model: str | None
    mmproj: str | None
    files: list[ListedOut]
    sources: list[SourceOut]
    pinned: str | None
    preferred: str | None
    items: list[ItemOut]
    running: bool
    inbox: str
    inbox_files: list[InboxFileOut]
    free: int
    """Free space on the disk of the data folder (bytes)."""


class StartIn(BaseModel):
    files: list[str] = Field(min_length=1, max_length=10)
    source: str | None = None
    """A source chosen under Advanced; None: automatic."""


class FileIn(BaseModel):
    path: str


class ChosenOut(BaseModel):
    path: str | None


def _bad(err: DownloadError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, render(err.message))


@router.get("", summary="首次启动向导的状态")
def setup_state(owner: Manager, inbox: Annotated[bool, Query()] = False) -> SetupOut:
    """What the wizard shows. inbox=true also checks the inbox folder (the wizard asks every two
    seconds while it shows that option) and imports complete listed files."""
    import shutil

    from jacara.core.config import get_settings
    from jacara.modules.hub import registry

    DOWNLOADS.restore_paused()
    inbox_files: list[dict[str, Any]] = DOWNLOADS.scan_inbox(owner) if inbox else []
    settings = get_settings()
    model, mmproj = settings.find_model(), settings.find_mmproj()
    in_library = {f.sha256 for v in registry.versions() for f in v.files}
    state = read_state()
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return SetupOut(
        needs_model=model is None,
        model=model.name if model else None,
        mmproj=mmproj.name if mmproj else None,
        files=[
            ListedOut(
                filename=f.filename,
                slot=f.slot,
                name=f.name,
                size=f.size,
                license=f.license,
                default=f.default,
                in_library=f.sha256 in in_library,
            )
            for f in DOWNLOADS.listed()
        ],
        sources=[SourceOut(id=k, label=tr(v)) for k, v in SOURCES.items()],
        pinned=state.get("pinned"),
        preferred=state.get("preferred"),
        items=[ItemOut(**i) for i in DOWNLOADS.view()],
        running=DOWNLOADS.running(),
        inbox=str(inbox_dir()),
        inbox_files=[InboxFileOut(**f) for f in inbox_files],
        free=shutil.disk_usage(folder).free,
    )


@router.post("/downloads", status_code=status.HTTP_202_ACCEPTED, summary="开始下载模型")
def start_download(data: StartIn, owner: Manager) -> None:
    require(owner, "system:manage")
    try:
        DOWNLOADS.start(data.files, owner, data.source)
    except DownloadError as err:
        raise _bad(err) from err


@router.post("/pause", status_code=status.HTTP_204_NO_CONTENT, summary="暂停下载")
def pause_download(owner: Manager) -> None:
    require(owner, "system:manage")
    DOWNLOADS.pause()


@router.post("/resume", status_code=status.HTTP_202_ACCEPTED, summary="继续下载")
def resume_download(owner: Manager) -> None:
    require(owner, "system:manage")
    DOWNLOADS.resume(owner)


@router.post("/cancel", status_code=status.HTTP_204_NO_CONTENT, summary="取消下载")
def cancel_download(owner: Manager) -> None:
    """Stop and delete the partial files. Models already imported stay in the model library."""
    require(owner, "system:manage")
    DOWNLOADS.cancel()


@router.post("/open-inbox", status_code=status.HTTP_204_NO_CONTENT, summary="打开模型收件文件夹")
def open_inbox(owner: Manager) -> None:
    del owner
    folder = inbox_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        open_folder(folder)
    except DesktopFileError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("找不到这个文件夹")) from err


@router.post("/choose-file", summary="用系统对话框选择模型文件")
def choose_model_file(owner: Manager) -> ChosenOut:
    del owner
    try:
        chosen = choose_file(tr("选择模型文件（.gguf）"), ("gguf",))
    except DesktopFileError as err:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            tr("这台电脑无法打开系统的选择对话框，请直接输入完整路径"),
        ) from err
    return ChosenOut(path=str(chosen) if chosen else None)


@router.post("/files", status_code=status.HTTP_202_ACCEPTED, summary="导入电脑上的模型文件")
def import_model_file(data: FileIn, owner: Manager) -> None:
    """Copy a model file into the model library (the original stays) and use it. Files of the list
    are checked against it; other .gguf files are imported as they are."""
    require(owner, "system:manage")
    path = Path(data.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("请填写完整路径"))
    try:
        DOWNLOADS.add_file(path, owner)
    except DownloadError as err:
        raise _bad(err) from err
