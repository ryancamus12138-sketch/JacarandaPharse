"""The downloads of this process: one background thread works through the files in order
(download, check, import), and the page follows them through GET /api/v1/setup. Nothing starts
without the user: after a restart the partial files are shown as paused."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jacara.core.i18n import join_items, render, stored, tr
from jacara.downloads import (
    SOURCES,
    DownloadError,
    Item,
    LockedFile,
    SourceFailed,
    Stopped,
    check_space,
    downloads_dir,
    fetch,
    inbox_dir,
    load_lock,
    part_of,
    read_state,
    sha256_of,
    source_order,
    write_state,
)

if TYPE_CHECKING:
    from jacara.core.owner import Owner

log = logging.getLogger(__name__)

CUSTOM_NAME = re.compile(r"[^A-Za-z0-9._-]+")

ACTIVE = ("waiting", "downloading", "verifying", "importing")
"""Item states in which the file is being worked on."""


class Downloads:
    def __init__(self, lock: tuple[LockedFile, ...] | None = None) -> None:
        self._lock = threading.Lock()
        self._listed = lock
        self.items: dict[str, Item] = {}
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self.owner: Owner | None = None
        self.inbox_seen: dict[str, tuple[int, float]] = {}
        """Inbox file → (size, modification time) at the last check: a file is checked once it
        has the listed size and did not change between two checks (copying has finished)."""
        self.inbox_failed: dict[str, tuple[int, float]] = {}
        """Inbox files that failed the check, not checked again until they change."""

    # ---- What is offered ----

    def listed(self) -> tuple[LockedFile, ...]:
        if self._listed is None:
            self._listed = load_lock()
        return self._listed

    def locked(self, filename: str) -> LockedFile | None:
        return next((f for f in self.listed() if f.filename == filename), None)

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def restore_paused(self) -> None:
        """Partial files from an earlier run are shown as paused (continuing needs a click)."""
        with self._lock:
            for item in self.listed():
                part = part_of(item)
                if item.filename not in self.items and part.is_file():
                    self.items[item.filename] = Item(
                        item, state="paused", done=part.stat().st_size, total=item.size
                    )

    # ---- Controls ----

    def start(self, filenames: list[str], owner: Owner, pinned: str | None) -> None:
        """Download these files (a click in the wizard). pinned: a source chosen under Advanced,
        None for automatic."""
        if pinned is not None and pinned not in SOURCES:
            raise DownloadError(stored("未知的下载来源：{source}", source=pinned))
        chosen = [self.locked(name) for name in filenames]
        missing = [name for name, f in zip(filenames, chosen, strict=True) if f is None]
        if missing or not chosen:
            raise DownloadError(stored("不在模型清单中：{names}", names=join_items(missing)))
        state = read_state()
        state["pinned"] = pinned
        write_state(state)
        with self._lock:
            for item in (f for f in chosen if f is not None):
                current = self.items.get(item.filename)
                if current is None or current.state in ("paused", "failed", "done"):
                    if current is not None and current.state == "done":
                        continue
                    self.items[item.filename] = Item(item, total=item.size)
        self._run(owner)

    def resume(self, owner: Owner) -> None:
        with self._lock:
            for item in self.items.values():
                if item.state in ("paused", "failed"):
                    item.state, item.error, item.switched = "waiting", None, []
        self._run(owner)

    def pause(self, wait: bool = True) -> None:
        """Stop after the block being written; partial files are kept."""
        thread = self.thread
        if thread is None or not thread.is_alive():
            return
        self.stopping.set()
        if wait:
            thread.join(timeout=60)
            # Files still waiting their turn are paused too, so that one click continues them all.
            with self._lock:
                for item in self.items.values():
                    if item.state == "waiting":
                        item.state = "paused"

    def cancel(self) -> None:
        """Stop and delete the partial downloads. Imported files stay in the model library."""
        self.pause()
        with self._lock:
            for name, item in list(self.items.items()):
                if item.state != "done":
                    if item.origin == "download":
                        part_of(item.file).unlink(missing_ok=True)
                    if item.copy is not None:
                        item.copy.unlink(missing_ok=True)
                    del self.items[name]
        state = read_state()
        state.pop("etags", None)
        write_state(state)

    def _run(self, owner: Owner) -> None:
        self.owner = owner
        if self.running():
            return
        self.stopping.clear()
        self.thread = threading.Thread(target=self._work, name="model-downloads", daemon=True)
        self.thread.start()

    # ---- The work ----

    def _next(self) -> Item | None:
        with self._lock:
            return next((i for i in self.items.values() if i.state == "waiting"), None)

    def _work(self) -> None:
        while not self.stopping.is_set():
            item = self._next()
            if item is None:
                return
            try:
                if item.origin == "download":
                    self._download(item)
                elif item.origin == "file" or (
                    item.origin == "inbox" and item.path is not None and item.path.is_symlink()
                ):
                    # A link is not moved into the model library: the library would only hold the
                    # link, and the file it points to could change or disappear.
                    self._copy(item)
                self._verify(item)
                self._import(item)
                item.state = "done"
            except Stopped:
                item.state = "paused"
                return
            except DownloadError as err:
                item.state, item.error = "failed", err.message
            except Exception as err:  # Shown to the user; the details go to the log.
                log.exception("model download failed")
                item.state, item.error = "failed", stored("出错了：{error}", error=str(err))

    def _download(self, item: Item) -> None:
        file = item.file
        part = part_of(file)
        if self._already_imported(item):
            return
        have = part.stat().st_size if part.exists() else 0
        check_space(downloads_dir(), file.size - have)
        state = read_state()
        item.state, item.switched, item.error = "downloading", [], None
        for source in source_order(file, state):
            item.source = source.id
            try:
                fetch(item, source, part, state, self.stopping)
                state = read_state()
                state["preferred"] = source.id
                write_state(state)
                return
            except SourceFailed as failed:
                item.switched.append((source.id, failed.reason))
                log.warning(
                    "download %s from %s failed: %s",
                    file.filename,
                    source.id,
                    render(failed.reason),
                )
        # The reason of each source is shown with the file (switched).
        raise DownloadError(stored("所有下载来源都失败了"))

    def _already_imported(self, item: Item) -> bool:
        """The model library already has this exact file (same SHA-256): use it, no download."""
        from jacara.modules.hub import registry

        for version in registry.versions(item.file.name):
            if any(f.sha256 == item.file.sha256 for f in version.files):
                item.imported = f"{version.name}@v{version.version}"
                item.done = item.total = item.file.size
                return True
        return False

    def _copy(self, item: Item) -> None:
        """A file chosen on this computer or an inbox link: copy it next to the downloads (the
        original stays)."""
        source = item.path
        if source is None:
            raise DownloadError(stored("没有选择文件"))
        # Not the .part name: a download of the same file may be paused there.
        target = downloads_dir() / f"{item.file.filename}.copy"
        check_space(downloads_dir(), source.stat().st_size)
        item.state, item.done, item.total = "downloading", 0, source.stat().st_size
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, target.open("wb") as dst:
            while block := src.read(1024 * 1024):
                if self.stopping.is_set():
                    raise Stopped
                dst.write(block)
                item.done += len(block)
        item.copy = target

    def _verify(self, item: Item) -> None:
        if item.imported:
            return
        path = self._path(item)
        item.state, item.done = "verifying", 0
        item.total = path.stat().st_size
        if item.custom:
            return
        if item.total != item.file.size:
            raise DownloadError(
                stored(
                    "文件大小与清单不符：{name}（{size} 字节，应为 {expected} 字节）",
                    name=item.file.filename,
                    size=item.total,
                    expected=item.file.size,
                )
            )

        def advance(n: int) -> None:
            if self.stopping.is_set():
                raise Stopped
            item.done += n

        digest = sha256_of(path, advance)
        if digest == item.file.sha256:
            return
        if item.copy is not None:
            item.copy.unlink(missing_ok=True)  # The copy; the file itself is untouched.
            item.copy = None
        if item.origin == "inbox":
            raise DownloadError(
                stored(
                    "校验不一致：{name} 与清单中的文件内容不同。文件留在原处，请删除后重新下载",
                    name=item.file.filename,
                )
            )
        if item.origin == "file":
            raise DownloadError(
                stored("校验不一致：{name} 与清单中的文件内容不同", name=item.file.filename)
            )
        # A damaged download: start this file once more, then give up.
        path.unlink(missing_ok=True)
        if item.retried:
            raise DownloadError(
                stored("下载的文件校验不一致：{name}，已删除，请重试", name=item.file.filename)
            )
        item.retried = True
        self._download(item)
        self._verify(item)

    def _import(self, item: Item) -> None:
        """Move the checked file into the model library as a new version and put it to use."""
        from jacara.core.config import get_settings
        from jacara.core.db import session_scope
        from jacara.modules.audit.service import record
        from jacara.modules.hub import registry
        from jacara.modules.hub.service import HubService

        owner = self.owner
        if owner is None:
            raise DownloadError(stored("没有开始下载的用户"))
        item.state = "importing"
        with session_scope() as session:
            hub = HubService(session, owner, get_settings())
            if item.imported is None:
                path = self._path(item)
                work = registry.incoming()
                os.replace(path, work / item.file.filename)
                file = item.file
                meta: dict[str, Any] = {
                    "base_model": file.base_model,
                    "license": file.license,
                    "imported_by": hub._who(),
                }
                if not item.custom:
                    meta["source"] = next(
                        (s.url for s in file.sources if s.id == item.source), file.sources[0].url
                    )
                version = registry.add_version(
                    file.name, file.slot, work, file.filename, None, meta, file.description
                )
                item.imported = f"{version.name}@v{version.version}"
                record(
                    session,
                    owner,
                    "hub:model_download" if item.origin == "download" else "hub:model_import",
                    "model",
                    item.imported,
                    kind=version.kind,
                    origin=item.origin,
                    source=item.source,
                    files=[
                        {"name": f.name, "sha256": f.sha256, "size": f.size} for f in version.files
                    ],
                )
            name, number = item.imported.rsplit("@v", 1)
            hub.use(name, int(number))
        if item.origin == "download":
            state = read_state()
            (state.get("etags") or {}).pop(item.file.filename, None)
            write_state(state)
        item.done = item.total

    @staticmethod
    def _path(item: Item) -> Path:
        """The file to check and import: the partial download, the copy or the inbox file."""
        if item.origin == "download":
            return part_of(item.file)
        if item.copy is not None:
            return item.copy
        if item.path is None:
            raise DownloadError(stored("没有选择文件"))
        return item.path

    # ---- Inbox and chosen files ----

    def scan_inbox(self, owner: Owner) -> list[dict[str, Any]]:
        """Files in the inbox and what happens to them. Nothing needs confirming: a listed file
        that has the listed size and did not change between two checks is checked and imported
        on its own, and its row shows how far that has got."""
        from jacara.modules.hub import registry

        folder = inbox_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # Files that failed their check are not checked again until they change.
        for item in list(self.items.values()):
            if item.origin == "inbox" and item.state == "failed" and item.path is not None:
                with contextlib.suppress(OSError):
                    stat = item.path.stat()
                    self.inbox_failed[item.path.name] = (stat.st_size, stat.st_mtime)
        library = {f.sha256 for v in registry.versions() for f in v.files}
        found: list[dict[str, Any]] = []
        ready: list[Item] = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            stat = path.stat()
            key = (stat.st_size, stat.st_mtime)
            file = self.locked(path.name)
            done: int | None = None
            total: int | None = None
            if file is None:
                status = "unknown" if path.suffix.lower() == ".gguf" else "ignored"
            elif stat.st_size < file.size:
                status = "copying"
            elif stat.st_size > file.size:
                status = "wrong_size"
            elif self.inbox_failed.get(path.name) == key:
                status = "failed"
            elif self.inbox_seen.get(path.name) != key:
                status = "copying"
            else:
                current = self.items.get(file.filename)
                if current is not None and current.state in ACTIVE:
                    if current.origin == "inbox" and current.path == path:
                        # Copying a link counts as putting the file into the library.
                        status = "importing" if current.state == "downloading" else current.state
                        done, total = current.done, current.total
                    else:
                        status = "busy"
                elif file.sha256 in library:
                    status = "in_library"
                else:
                    status = "waiting"
                    ready.append(Item(file, origin="inbox", path=path, total=file.size))
            self.inbox_seen[path.name] = key
            found.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "expected": file.size if file else None,
                    "status": status,
                    "done": done,
                    "total": total,
                }
            )
        if ready:
            with self._lock:
                for item in ready:
                    self.items[item.file.filename] = item
            self._run(owner)
        return found

    def add_file(self, path: Path, owner: Owner) -> None:
        """A model file chosen on this computer. Listed files (same name) are checked against the
        list; other .gguf files are imported as they are, as a model or (name containing
        mmproj) a vision model."""
        from jacara.core.config import is_mmproj

        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise DownloadError(stored("请选择 .gguf 模型文件"))
        file = self.locked(path.name)
        custom = file is None
        if file is None:
            stem = CUSTOM_NAME.sub("-", path.stem).strip("-._")[:64] or "model"
            file = LockedFile(
                slot="mmproj" if is_mmproj(path) else "model",
                name=stem,
                filename=path.name,
                size=path.stat().st_size,
                sha256="0" * 64,
                license="",
                base_model="",
                default=False,
                sources=(),
            )
        with self._lock:
            self.items[file.filename] = Item(file, origin="file", path=path, custom=custom)
        self._run(owner)

    # ---- What the page shows ----

    def view(self) -> list[dict[str, Any]]:
        out = []
        for item in list(self.items.values()):
            remaining = max(item.total - item.done, 0)
            out.append(
                {
                    "filename": item.file.filename,
                    "slot": item.file.slot,
                    "origin": item.origin,
                    "state": item.state,
                    "done": item.done,
                    "total": item.total,
                    "speed": round(item.speed),
                    "eta": round(remaining / item.speed) if item.speed > 0 else None,
                    "source": item.source,
                    "switched": [
                        {"source": s, "label": tr(SOURCES[s]), "reason": render(r)}
                        for s, r in item.switched
                    ],
                    "error": render(item.error) if item.error else None,
                    "imported": item.imported,
                    "custom": item.custom,
                }
            )
        return out


DOWNLOADS = Downloads()
