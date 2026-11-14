"""The downloads of this process: one background thread works through the files in order
(download, check, import), and the page follows them through GET /api/v1/setup. Nothing starts
without the user: after a restart the partial files are shown as paused."""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from pathlib import Path, PurePosixPath
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
    models_dir,
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

PARTIAL = (".crdownload", ".part", ".download")
"""Names browsers give a file while they are still downloading it (Chrome .crdownload, Firefox
.part, Safari .download): left alone until the browser gives it its real name."""

ONNX_HINTS = (
    ("table", re.compile(r"slanet|table")),
    ("ocr_det", re.compile(r"(?<![a-z])det(ection)?(?![a-z])")),
    ("ocr_rec", re.compile(r"(?<![a-z])rec(ognition)?(?![a-z])")),
)
"""Kind suggested for an .onnx file from the words in its path, as PaddleOCR names its models
(PP-OCRv5_server_det, PP-OCRv5_server_rec, SLANet_plus). Only a suggestion: the user chooses."""

GGUF_KINDS = ("model", "mmproj")
ONNX_KINDS = ("ocr_det", "ocr_rec", "table")


def custom_name(stem: str) -> str:
    """Model library name for a file imported as it is."""
    return CUSTOM_NAME.sub("-", stem).strip("-._")[:64] or "model"


def onnx_hint(rel: str) -> str | None:
    lower = rel.lower()
    return next((kind for kind, words in ONNX_HINTS if words.search(lower)), None)


def _row(name: str, size: int, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "size": size,
        "expected": None,
        "status": status,
        "done": None,
        "total": None,
        "error": None,
        "kind": None,
        "importable": False,
        "deletable": True,
        "link": False,
        "target": None,
        "legacy": False,
        "parts": None,
        "count": None,
        "dictionaries": [],
        "suggested": None,
        **extra,
    }


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
        self.inbox_other = 0
        """Other files in the inbox at the last check (not model files): only counted."""

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
                    item.origin == "inbox"
                    and item.path is not None
                    and item.path.is_symlink()
                    and not item.extra
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
            if item.origin == "inbox":
                # Checked before anything leaves the inbox: the model library removes a version it
                # refuses.
                from jacara.modules.hub import registry

                try:
                    registry.check(item.kind or item.file.slot, path, item.dictionary)
                except registry.HubError as err:
                    raise DownloadError(err.message) from err
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
        if (
            item.origin == "inbox"
            and item.path is not None
            and item.path.name != item.file.filename
        ):
            raise DownloadError(
                stored(
                    "{name} 与清单中的 {listed} 大小相同，但内容不同，不是这个文件",
                    name=item.rel or item.path.name,
                    listed=item.file.filename,
                )
            )
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
        """Move the checked file into the model library as a new version and put it to use (OCR
        models: only into the library; they are switched to on the settings page)."""
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
                work = registry.incoming()
                leave = self._gather(item, work)
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
                    file.name,
                    item.kind or file.slot,
                    work,
                    file.filename,
                    item.dictionary.name if item.dictionary else None,
                    meta,
                    file.description,
                )
                # In the library now: the inbox files that were linked or copied leave the inbox.
                for path in leave:
                    path.unlink(missing_ok=True)
                    _prune(path.parent)
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
            if item.use:
                hub.use(name, int(number))
        if item.origin == "download":
            state = read_state()
            (state.get("etags") or {}).pop(item.file.filename, None)
            write_state(state)
        item.done = item.total

    def _gather(self, item: Item, work: Path) -> list[Path]:
        """Put the files of item into work for the model library. Returns the inbox files to remove
        once the library has taken them.

        A partial download or a copy made by this program is moved. An inbox file is hard-linked
        (copied where the disk cannot) and removed from the inbox only after the import: if the
        library refuses it, the file is still where the user put it. A symbolic link is copied, so
        the file it points to stays where it is. The recognition dictionary is copied."""
        leave: list[Path] = []
        main = self._path(item)
        if item.origin != "inbox" or item.copy is not None:
            os.replace(main, work / item.file.filename)
        elif self._bring(main, work / item.file.filename):
            leave.append(main)
        for path in item.extra:
            if self._bring(path, work / path.name):
                leave.append(path)
        if item.dictionary is not None:
            shutil.copyfile(item.dictionary, work / item.dictionary.name)
        return leave

    @staticmethod
    def _bring(source: Path, target: Path) -> bool:
        """Hard-link (or copy) an inbox file into target. Returns whether it is to leave the inbox
        afterwards (not for links: the file they point to is the user's)."""
        if source.is_symlink():
            shutil.copyfile(source, target)
            return False
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        return True

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
        """Model files in the inbox, at any depth, and loose .gguf files in the models folder (where
        earlier versions kept them), with what happens to them. Rows are named by their path
        relative to the models folder.

        Nothing needs confirming for listed files: a file with the size of a listed file that did
        not change between two checks is checked and imported, also when it was renamed (in a
        Hugging Face cache, blobs/ holds the files under their hash). Other .gguf and .onnx files
        are imported when the user asks (import_inbox). Loose files in the models folder are only
        imported when asked too."""
        from jacara.core.config import is_mmproj
        from jacara.modules.hub import registry

        root = models_dir()
        # Files that failed their check are not checked again until they change.
        for item in list(self.items.values()):
            if item.origin == "inbox" and item.state == "failed" and item.rel is not None:
                key = _key([p for p in (item.path, *item.extra) if p is not None])
                if key is not None:
                    self.inbox_failed[item.rel] = key
        library = {f.sha256 for v in registry.versions() for f in v.files}
        sizes = {f.size: f for f in self.listed()}
        files, links = self._walk()
        other = 0
        rows: list[dict[str, Any]] = []
        ready: list[Item] = []
        claimed: set[str] = set()
        gguf: list[Path] = []
        onnx: list[Path] = []
        texts: dict[Path, list[str]] = {}
        for path in files:
            rel = self._rel(path)
            if path.name.lower().endswith(PARTIAL):
                rows.append(_row(rel, _size(path), "copying"))
                continue
            try:
                stat = path.stat()
            except OSError:  # A link to a file that is gone.
                other += 1
                continue
            suffix = path.suffix.lower()
            loose = path.parent == root
            file = self.locked(path.name)
            renamed = False
            if file is None and suffix in (".gguf", "") and stat.st_size in sizes:
                file, renamed = sizes[stat.st_size], True
            key = (stat.st_size, stat.st_mtime)
            if file is not None and renamed and self.inbox_failed.get(rel) == key:
                file = None  # Same size, other content: not the listed file.
            if file is not None and not loose:
                rows.append(
                    self._listed_row(path, rel, key, file, renamed, library, ready, claimed)
                )
            elif suffix == ".gguf":
                gguf.append(path)
            elif suffix == ".onnx":
                onnx.append(path)
            elif suffix == ".txt":
                texts.setdefault(path.parent, []).append(rel)
            else:
                other += 1
        for group in _groups(gguf):
            kind = "mmproj" if is_mmproj(group[0]) else "model"
            rows.append(self._custom_row(group, kind, []))
        for path in onnx:
            rows.append(
                self._custom_row([path], onnx_hint(self._rel(path)), texts.get(path.parent, []))
            )
        rows += [_row(self._rel(p), 0, "folder_link") for p in links]
        self.inbox_other = other
        if ready:
            with self._lock:
                for item in ready:
                    self.items[item.file.filename] = item
            self._run(owner)
        for row in rows:
            path = root.joinpath(*PurePosixPath(row["name"]).parts)
            row["link"] = path.is_symlink()
            row["target"] = str(path.resolve()) if row["link"] else None
            # Outside the inbox: where earlier versions kept models, not part of the desktop app's
            # models folder (hub/, inbox/, downloads/, selected.json).
            row["legacy"] = PurePosixPath(row["name"]).parts[0] != "inbox"
            # Not while it is being worked on; a folder (Safari's download package) only by hand.
            row["deletable"] = row["status"] not in ("waiting", "verifying", "importing") and (
                row["link"] or not path.is_dir()
            )
        return sorted(rows, key=lambda r: r["name"])

    def _walk(self) -> tuple[list[Path], list[Path]]:
        """Files under the inbox at any depth, plus the model files where earlier versions kept
        them. Hidden files and folders are skipped. Returns (files, links to folders). Links to
        folders are not entered (os.walk does not follow them), so a link cannot make the scan go
        round in circles; a Safari .download package is a folder and is listed as a file being
        downloaded."""
        inbox = inbox_dir()
        inbox.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        links: list[Path] = []
        for top, dirs, names in os.walk(inbox):
            here = Path(top)
            entered = []
            for name in sorted(dirs):
                path = here / name
                if name.startswith("."):
                    continue
                if path.is_symlink():
                    links.append(path)
                elif name.lower().endswith(PARTIAL):
                    files.append(path)
                else:
                    entered.append(name)
            dirs[:] = entered
            files += [here / n for n in sorted(names) if not n.startswith(".")]
        # Where earlier versions kept models: loose in models/, OCR models in models/ocr/, table
        # models in models/table/. The desktop app runs imported models only, so these are offered.
        root = models_dir()
        files += sorted(p for p in root.glob("*.gguf") if not p.name.startswith("."))
        for folder, patterns in (("ocr", ("*.onnx", "*.txt")), ("table", ("*.onnx",))):
            for pattern in patterns:
                files += sorted(
                    p
                    for p in (root / folder).rglob(pattern)
                    if p.is_file() and not any(part.startswith(".") for part in p.parts)
                )
        return files, links

    @staticmethod
    def _rel(path: Path) -> str:
        return path.relative_to(models_dir()).as_posix()

    def _at(self, rel: str) -> Item | None:
        """The item working on (or done with) this inbox file."""
        return next(
            (i for i in list(self.items.values()) if i.origin == "inbox" and i.rel == rel), None
        )

    def _listed_row(
        self,
        path: Path,
        rel: str,
        key: tuple[int, float],
        file: LockedFile,
        renamed: bool,
        library: set[str],
        ready: list[Item],
        claimed: set[str],
    ) -> dict[str, Any]:
        stable = self.inbox_seen.get(rel) == key
        self.inbox_seen[rel] = key
        row = _row(rel, key[0], "copying", expected=file.size, kind=file.slot)
        mine = self._at(rel)
        other = self.items.get(file.filename)
        if key[0] < file.size:
            return row
        if key[0] > file.size:
            return {**row, "status": "wrong_size"}
        if self.inbox_failed.get(rel) == key:
            error = render(mine.error) if mine is not None and mine.error else None
            return {**row, "status": "failed", "error": error}
        if not stable:
            return row
        if mine is not None and mine.state in ACTIVE:
            # Copying a link counts as putting the file into the library.
            state = "importing" if mine.state == "downloading" else mine.state
            return {**row, "status": state, "done": mine.done, "total": mine.total}
        if (other is not None and other.state in ACTIVE) or file.filename in claimed:
            return {**row, "status": "busy"}
        if mine is not None and mine.state == "done":
            return {**row, "status": "in_library"}
        if file.sha256 in library:
            return {**row, "status": "same_size_in_library" if renamed else "in_library"}
        claimed.add(file.filename)
        ready.append(Item(file, origin="inbox", path=path, rel=rel, total=file.size))
        return {**row, "status": "waiting"}

    def _custom_row(
        self, paths: list[Path], kind: str | None, dictionaries: list[str]
    ) -> dict[str, Any]:
        """A model file not in the list (or a loose file in the models folder), or the files of a
        split GGUF model: imported when the user asks."""
        from jacara.modules.hub import registry

        first = paths[0]
        rel = self._rel(first)
        key = _key(paths)
        size = key[0] if key is not None else 0
        stable = key is not None and self.inbox_seen.get(rel) == key
        if key is not None:
            self.inbox_seen[rel] = key
        split = registry.SPLIT.fullmatch(first.name)
        stem = split["stem"] if split else first.stem
        listed = self.locked(first.name)
        row = _row(
            rel,
            size,
            "copying",
            kind=listed.slot if listed is not None and listed.size == size else kind,
            dictionaries=dictionaries,
            suggested=listed.name if listed is not None else custom_name(stem),
            parts=len(paths) if split else None,
            count=int(split["count"]) if split else None,
        )
        mine = self._at(rel)
        if mine is not None and mine.state in ACTIVE:
            state = "importing" if mine.state == "downloading" else mine.state
            return {**row, "status": state, "done": mine.done, "total": mine.total}
        if mine is not None and mine.state == "done":
            return {**row, "status": "in_library"}
        if split and (int(split["part"]) != 1 or len(paths) != int(split["count"])):
            return {**row, "status": "shards_missing"}
        if not stable:
            return row
        if mine is not None and mine.state == "failed" and self.inbox_failed.get(rel) == key:
            error = render(mine.error) if mine.error else None
            return {**row, "status": "failed", "error": error, "importable": True}
        return {**row, "status": "unknown", "importable": True}

    def import_inbox(
        self, rel: str, kind: str, name: str, dictionary: str | None, owner: Owner
    ) -> None:
        """Import a file shown in the inbox list when the user asks: a listed file (checked against
        the list), or any .gguf / .onnx file as it is, as the chosen kind. A split GGUF model is
        imported with all its files, a recognition model with the chosen dictionary. .gguf files
        are put to use; OCR models are switched to on the settings page."""
        from jacara.modules.hub import registry

        path = self._resolve(rel)
        if kind not in registry.KINDS:
            raise DownloadError(stored("未知种类：{kind}", kind=kind))
        suffix = registry.FORMATS[kind]
        if path.suffix.lower() != suffix:
            raise DownloadError(
                stored("{kind}需要 {suffix} 文件", kind=stored(registry.KINDS[kind]), suffix=suffix)
            )
        listed = self.locked(path.name)
        if listed is not None and listed.slot == kind and path.stat().st_size == listed.size:
            item = Item(listed, origin="inbox", path=path, rel=rel, total=listed.size)
            slot = listed.filename
        else:
            try:
                registry.precheck(name, kind)
            except registry.HubError as err:
                raise DownloadError(err.message) from err
            extra: list[Path] = []
            split = registry.SPLIT.fullmatch(path.name)
            if split is not None:
                extra = [path.parent / p for p in registry.split_parts(path.name)]
                missing = [p.name for p in extra if not p.is_file()]
                if int(split["part"]) != 1 or missing:
                    raise DownloadError(
                        stored("分片模型缺少文件：{files}", files=join_items(missing or [rel]))
                    )
            chosen = None
            if dictionary:
                if kind != "ocr_rec":
                    raise DownloadError(stored("仅识别模型需要字典"))
                chosen = self._resolve(dictionary)
            size = sum(p.stat().st_size for p in (path, *extra))
            file = LockedFile(
                slot="model",
                name=name,
                filename=path.name,
                size=size,
                sha256="0" * 64,
                license="",
                base_model="",
                default=False,
                sources=(),
            )
            item = Item(
                file,
                origin="inbox",
                path=path,
                rel=rel,
                total=size,
                custom=True,
                kind=kind,
                extra=extra,
                dictionary=chosen,
                # An OCR model is switched to on the settings page, unless the moved file is the
                # one in use (a loose file of an earlier version): then the import takes its place.
                use=kind in GGUF_KINDS or _in_use(kind, path),
            )
            slot = f"inbox:{rel}"
        with self._lock:
            current = self.items.get(slot)
            if current is not None and current.state in ACTIVE:
                raise DownloadError(stored("这个文件正在处理"))
            self.items[slot] = item
            self.inbox_failed.pop(rel, None)
        self._run(owner)

    @staticmethod
    def _resolve(rel: str) -> Path:
        """An inbox file (or a loose file in the models folder) from its name on the page."""
        path = _place(rel)
        if not path.is_file():
            raise DownloadError(stored("找不到这个文件：{name}", name=rel))
        return path

    def delete_inbox(self, names: list[str], owner: Owner) -> list[str]:
        """Delete files shown in the inbox list, when the user asks. A link is removed, not the
        file it points to; a split model's row removes all its files. Folders left empty inside the
        inbox go too. Returns what was deleted (paths relative to the models folder)."""
        from jacara.core.db import session_scope
        from jacara.modules.audit.service import record

        targets: list[Path] = []
        for rel in names:
            path = _place(rel)
            if not path.is_symlink() and not path.exists():
                raise DownloadError(stored("找不到这个文件：{name}", name=rel))
            if path.is_dir() and not path.is_symlink():
                raise DownloadError(stored("{name} 是文件夹，请在文件管理器中删除", name=rel))
            for target in _family(path):
                mine = self._at(self._rel(target))
                if mine is not None and mine.state in ACTIVE:
                    raise DownloadError(stored("这个文件正在处理"))
                targets.append(target)
        deleted: list[str] = []
        for path in dict.fromkeys(targets):
            rel = self._rel(path)
            path.unlink(missing_ok=True)
            self.inbox_seen.pop(rel, None)
            self.inbox_failed.pop(rel, None)
            deleted.append(rel)
            _prune(path.parent)
        if deleted:
            with session_scope() as session:
                record(session, owner, "hub:inbox_delete", "model", None, files=deleted)
        return deleted

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
                    "slot": item.kind or item.file.slot,
                    "rel": item.rel,
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


def _place(rel: str) -> Path:
    """A path shown in the inbox list (relative to the models folder): in the inbox, or where
    earlier versions kept models (models/, models/ocr/, models/table/). Nothing else."""
    parts = PurePosixPath(rel).parts
    inside = len(parts) == 1 or (len(parts) > 1 and parts[0] in ("inbox", "ocr", "table"))
    if not parts or PurePosixPath(rel).is_absolute() or ".." in parts or not inside:
        raise DownloadError(stored("找不到这个文件：{name}", name=rel))
    return models_dir().joinpath(*parts)


def _family(path: Path) -> list[Path]:
    """The files a row stands for: every file of a split model that is there, or the file."""
    from jacara.modules.hub import registry

    split = registry.SPLIT.fullmatch(path.name)
    if split is None:
        return [path]
    count = int(split["count"])
    names = [f"{split['stem']}-{n:05d}-of-{count:05d}{path.name[-5:]}" for n in range(1, count + 1)]
    return [p for p in (path.parent / n for n in names) if p.is_symlink() or p.exists()]


FOLDER_METADATA = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
"""Files the system file manager writes into folders by itself: macOS Finder's .DS_Store (how the
folder is shown), Windows Explorer's Thumbs.db (thumbnail cache) and desktop.ini (folder view
settings). Recreated when needed; a folder holding only these counts as empty."""


def _prune(folder: Path) -> None:
    """Remove folders inside the inbox that are left empty, up to the inbox itself (as git does
    for the folders of removed files). A folder that only holds what the file manager wrote into it
    (FOLDER_METADATA) counts as empty; anything else in it keeps it."""
    inbox = inbox_dir()
    while folder != inbox and inbox in folder.parents:
        try:
            entries = list(folder.iterdir())
            if any(
                e.name.lower() not in FOLDER_METADATA or e.is_symlink() or not e.is_file()
                for e in entries
            ):
                return
            for entry in entries:
                entry.unlink()
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _in_use(kind: str, path: Path) -> bool:
    from jacara.core.config import get_settings

    settings = get_settings()
    if kind == "table":
        used = settings.table_model()
    else:
        used = settings.ocr_models().get(kind.removeprefix("ocr_"))
    return used is not None and used.absolute() == path.absolute()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _key(paths: list[Path]) -> tuple[int, float] | None:
    """Total size and latest modification time: unchanged between two checks means copying has
    finished."""
    try:
        stats = [p.stat() for p in paths]
    except OSError:
        return None
    return sum(s.st_size for s in stats), max((s.st_mtime for s in stats), default=0.0)


def _groups(paths: list[Path]) -> list[list[Path]]:
    """The .gguf files to show one row each: a split model's files together (same folder, stem and
    count, in part order), other files alone."""
    from jacara.modules.hub import registry

    groups: dict[tuple[Path, str, str], list[Path]] = {}
    single: list[list[Path]] = []
    for path in paths:
        split = registry.SPLIT.fullmatch(path.name)
        if split is None:
            single.append([path])
        else:
            groups.setdefault((path.parent, split["stem"].lower(), split["count"]), []).append(path)
    return single + [sorted(g, key=_part) for g in groups.values()]


def _part(path: Path) -> int:
    from jacara.modules.hub import registry

    split = registry.SPLIT.fullmatch(path.name)
    return int(split["part"]) if split else 0


DOWNLOADS = Downloads()
