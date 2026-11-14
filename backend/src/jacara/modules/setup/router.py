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
    rel: str | None = None
    """Inbox file: its path relative to the models folder."""


class InboxFileOut(BaseModel):
    name: str
    """Path relative to the models folder: inbox/… for the inbox, a bare file name for a loose file
    in the models folder."""
    size: int
    expected: int | None
    """Size of the listed file it is taken for."""
    status: Literal[
        "copying",
        "waiting",
        "verifying",
        "importing",
        "in_library",
        "same_size_in_library",
        "busy",
        "wrong_size",
        "failed",
        "unknown",
        "shards_missing",
        "folder_link",
    ]
    """copying: smaller than listed, still changing, or still being downloaded by a browser;
    waiting: complete, after the file being worked on; verifying: SHA-256 being checked; importing:
    being put into the model library; in_library: imported, or the library already has the listed
    file; same_size_in_library: another name with the size of a listed file the library already
    has (not checked again); busy: the same file is being downloaded or copied another way;
    wrong_size: larger than listed; failed: did not pass (error); unknown: a model file not in the
    list, or a loose file in the models folder, imported when asked; shards_missing: some files of a
    split model are not there (parts of count); folder_link: a link to a folder, not entered."""
    done: int | None
    total: int | None
    """Progress (bytes) while verifying or importing."""
    error: str | None = None
    kind: str | None = None
    """Model library kind it is taken for, or suggested for a file imported as it is (None: the
    user chooses)."""
    importable: bool = False
    """Can be imported with POST /setup/inbox/import."""
    parts: int | None = None
    count: int | None = None
    """Split GGUF model: files found and files it has."""
    dictionaries: list[str] = []
    """.txt files next to an .onnx file: recognition dictionaries to choose from."""
    suggested: str | None = None
    """Model library name suggested for the import."""
    deletable: bool = True
    """Can be deleted with POST /setup/inbox/delete (not while it is being worked on)."""
    link: bool = False
    """A symbolic link: deleting it leaves the file it points to."""
    target: str | None = None
    """For a link: the file or folder it points to."""
    legacy: bool = False
    """Outside the inbox, where earlier versions kept models (models/*.gguf, models/ocr/,
    models/table/): not part of the desktop app's models folder; import it or delete it."""


class SetupOut(BaseModel):
    needs_model: bool
    """No large model is found: the wizard is shown."""
    model_missing: str | None
    """Model file set with JACARA_MODEL that does not exist (this setting takes precedence over the
    model chosen in the app)."""
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
    inbox_other: int
    """Other files in the inbox (not model files): only counted."""
    kinds: dict[str, str]
    """Names of the model library kinds."""
    free: int
    """Free space on the disk of the data folder (bytes)."""


class StartIn(BaseModel):
    files: list[str] = Field(min_length=1, max_length=10)
    source: str | None = None
    """A source chosen under Advanced; None: automatic."""


class FileIn(BaseModel):
    path: str


class InboxImportIn(BaseModel):
    name: str
    """The file as listed (path relative to the models folder)."""
    kind: Literal["model", "mmproj", "ocr_det", "ocr_rec", "table"]
    model_name: str = Field(min_length=1, max_length=64)
    """Model library name (ignored for a listed file, which keeps its listed name)."""
    dictionary: str | None = None
    """Recognition dictionary (ocr_rec): one of the .txt files listed with it."""


class InboxDeleteIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=1000)
    """Files as listed (paths relative to the models folder)."""


class InboxDeleteOut(BaseModel):
    deleted: list[str]


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
    # JACARA_MODEL wins over the choice in the app, even when its file is gone: say so.
    missing = str(model) if model is not None and not model.is_file() else None
    if missing is not None:
        model = None
    in_library = {f.sha256 for v in registry.versions() for f in v.files}
    state = read_state()
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return SetupOut(
        needs_model=model is None,
        model_missing=missing,
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
        inbox_other=DOWNLOADS.inbox_other if inbox else 0,
        kinds={k: tr(v) for k, v in registry.KINDS.items()},
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
    if chosen is not None and chosen.suffix.lower() != ".gguf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, tr("请选择 .gguf 模型文件"))
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


@router.post(
    "/inbox/import", status_code=status.HTTP_202_ACCEPTED, summary="导入放入文件夹中的模型"
)
def import_inbox_file(data: InboxImportIn, owner: Manager) -> None:
    """Import a model file found in the inbox (or loose in the models folder) as the chosen kind:
    .gguf files are put to use, OCR models only go into the model library."""
    require(owner, "system:manage")
    try:
        DOWNLOADS.import_inbox(data.name, data.kind, data.model_name, data.dictionary, owner)
    except DownloadError as err:
        raise _bad(err) from err


@router.post("/inbox/delete", summary="删除放入文件夹中的文件")
def delete_inbox_files(data: InboxDeleteIn, owner: Manager) -> InboxDeleteOut:
    """Delete files shown in the inbox list (a link: only the link; a split model: all its
    files). Permanent: the files do not go to the trash."""
    require(owner, "system:manage")
    try:
        return InboxDeleteOut(deleted=DOWNLOADS.delete_inbox(data.names, owner))
    except DownloadError as err:
        raise _bad(err) from err
