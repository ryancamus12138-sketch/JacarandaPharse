"""Model downloads of the desktop app (ADR 0068 section 6, "First-start wizard and model download").

The files offered are listed in models.lock, shipped with the program and never fetched at run time:
each with its size, its SHA-256 and its address at each source, pinned to a fixed commit. Downloads
start only when the user clicks (the only network access besides checking for updates):

- Sources are tried in order (Hugging Face, ModelScope, hf-mirror.com; the source that worked last
  first); a source that cannot be reached, answers with an error or sends almost nothing for 30
  seconds (curl's --speed-time default, with its 1 byte/second default limit) is replaced by the
  next, as APT's mirror method does. The three sources serve byte-identical files, so the next one
  continues where the last stopped (HTTP Range) instead of starting over.
- The partial file stays in models/downloads/<file>.part: pausing, an interruption or closing the
  app keeps it, and the download continues from there. Continuing at the same source sends If-Range
  with the ETag it gave (MDN If-Range): a 200 instead of 206 means the file changed and it starts
  over.
- Only the addresses in the list are requested; redirects must stay on HTTPS; no cookies, accounts
  or user data are sent.
- A finished file is checked against its size and SHA-256 (a mismatch starts it once more, then
  fails), moved into the model library and put to use.

Files can also be placed in models/inbox/ (downloaded elsewhere), or chosen on this computer: the
same check and import apply."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from jacara.core.i18n import N_, stored

log = logging.getLogger(__name__)

Slot = Literal["model", "mmproj"]

SOURCES: dict[str, str] = {
    "huggingface": N_("Hugging Face"),
    "modelscope": N_("魔搭社区（ModelScope）"),
    "hf_mirror": N_("hf-mirror.com（社区镜像）"),
}
"""Source ids in priority order (ADR 0068): the official repository first, the community mirror
last."""

STALL_SECONDS = 30.0
"""A source sending fewer than MIN_SPEED bytes per second over this long is given up (curl
--speed-time default when only --speed-limit is set)."""

MIN_SPEED = 1
"""Bytes per second (curl's --speed-limit default)."""

CONNECT_SECONDS = 30.0
SPACE_MARGIN = 1024**3
"""Free space needed besides the rest of the file (1 GB, ADR 0068)."""

MAX_REDIRECTS = 10
CHUNK = 1024 * 1024

ALLOW_HTTP = True
"""Tests serve files over plain HTTP from a local server; the program itself requires HTTPS."""

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LockError(Exception):
    """models.lock is malformed (a packaging mistake; the build checks it)."""


class DownloadError(Exception):
    """Downloading or importing cannot continue; message is a stored message for the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SourceFailed(Exception):
    """This source failed; the next one is tried. reason is a stored message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Stopped(Exception):
    """Paused or cancelled by the user."""


@dataclass(frozen=True)
class Source:
    id: str
    url: str


@dataclass(frozen=True)
class LockedFile:
    slot: Slot
    name: str
    """Model library name the file is imported under."""
    filename: str
    size: int
    sha256: str
    license: str
    base_model: str
    default: bool
    sources: tuple[Source, ...]
    description: str = ""


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise LockError(f"not an https address: {url}")


def parse_lock(text: str) -> tuple[LockedFile, ...]:
    """Read models.lock (TOML): version = 1 and one [[files]] table per file with its
    [[files.sources]] (id, url)."""
    raw = tomllib.loads(text)
    if raw.get("version") != 1:
        raise LockError("models.lock version must be 1")
    found: list[LockedFile] = []
    for entry in raw.get("files", []):
        sources = tuple(Source(str(s["id"]), str(s["url"])) for s in entry.get("sources", []))
        if not sources:
            raise LockError(f"{entry.get('filename')}: no sources")
        for source in sources:
            if source.id not in SOURCES:
                raise LockError(f"unknown source {source.id}")
            _check_url(source.url)
            if not source.url.endswith("/" + str(entry["filename"])):
                raise LockError(f"{source.url} does not end with the file name")
        item = LockedFile(
            slot=entry["slot"],
            name=str(entry["name"]),
            filename=str(entry["filename"]),
            size=int(entry["size"]),
            sha256=str(entry["sha256"]),
            license=str(entry["license"]),
            base_model=str(entry.get("base_model", "")),
            default=bool(entry.get("default", True)),
            sources=sources,
            description=str(entry.get("description", "")),
        )
        if item.slot not in ("model", "mmproj"):
            raise LockError(f"{item.filename}: unknown slot {item.slot}")
        if item.size <= 0 or not SHA256.match(item.sha256):
            raise LockError(f"{item.filename}: size and sha256 are required")
        found.append(item)
    if [f.filename for f in found] != list(dict.fromkeys(f.filename for f in found)):
        raise LockError("duplicate file names")
    return tuple(found)


def load_lock() -> tuple[LockedFile, ...]:
    return parse_lock(files("jacara.downloads").joinpath("models.lock").read_text("utf-8"))


# ---- Places ----


def models_dir() -> Path:
    from jacara.core.config import data_home

    return data_home() / "models"


def downloads_dir() -> Path:
    return models_dir() / "downloads"


def inbox_dir() -> Path:
    """Files downloaded elsewhere are placed here; the wizard checks and imports them."""
    return models_dir() / "inbox"


def part_of(item: LockedFile) -> Path:
    return downloads_dir() / f"{item.filename}.part"


def _state_path() -> Path:
    return downloads_dir() / "state.json"


def read_state() -> dict[str, Any]:
    """{"preferred": source that worked last, "pinned": source chosen under Advanced (None:
    automatic), "etags": {file: {"source": id, "etag": value}}}."""
    try:
        data = json.loads(_state_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    os.replace(temporary, path)


def source_order(item: LockedFile, state: dict[str, Any]) -> list[Source]:
    """The pinned source alone, or every source with the one that worked last first."""
    pinned = state.get("pinned")
    if pinned:
        chosen = [s for s in item.sources if s.id == pinned]
        if chosen:
            return chosen
    ordered = sorted(item.sources, key=lambda s: list(SOURCES).index(s.id))
    preferred = state.get("preferred")
    return sorted(ordered, key=lambda s: s.id != preferred)


def sha256_of(path: Path, progress: Callable[[int], None] = lambda _n: None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
            progress(len(block))
    return digest.hexdigest()


def check_space(folder: Path, needed: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if free < needed + SPACE_MARGIN:
        raise DownloadError(
            stored(
                "磁盘空间不足：需要 {need:.1f} GB，可用 {free:.1f} GB",
                need=(needed + SPACE_MARGIN) / 1e9,
                free=free / 1e9,
            )
        )


# ---- One download, one source ----


@dataclass
class Item:
    """One file being downloaded, checked or imported, as the page shows it."""

    file: LockedFile
    origin: Literal["download", "inbox", "file"] = "download"
    state: Literal[
        "waiting", "downloading", "verifying", "importing", "done", "paused", "failed"
    ] = "waiting"
    done: int = 0
    total: int = 0
    source: str | None = None
    switched: list[tuple[str, str]] = field(default_factory=list)
    """Sources given up in this attempt, with the reason (stored message)."""
    error: str | None = None
    """Stored message when failed."""
    speed: float = 0.0
    path: Path | None = None
    """Inbox or chosen file (origin inbox / file)."""
    copy: Path | None = None
    """Copy next to the downloads that is checked and imported instead of path: a chosen file, or
    an inbox entry that is a symbolic link (the file it points to stays where it is)."""
    custom: bool = False
    """A chosen file not in models.lock: imported as it is, without checking against the list."""
    imported: str | None = None
    """Model library version once imported ("name@v2")."""
    retried: bool = False
    """A download that failed its check was started once more."""


def stalled() -> str:
    return stored("连续 {seconds} 秒几乎没有收到数据", seconds=int(STALL_SECONDS))


class Meter:
    """Speed over the last few seconds, and the stall rule (fewer than MIN_SPEED bytes per second
    over STALL_SECONDS)."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, int]] = []
        self.window_start = time.monotonic()
        self.window_bytes = 0

    def add(self, n: int) -> None:
        now = time.monotonic()
        self.samples.append((now, n))
        self.samples = [(t, b) for t, b in self.samples if now - t <= 5]
        self.window_bytes += n
        self.check(now)

    def check(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if now - self.window_start >= STALL_SECONDS:
            if self.window_bytes < MIN_SPEED * STALL_SECONDS:
                raise SourceFailed(
                    stored("连续 {seconds} 秒几乎没有收到数据", seconds=int(STALL_SECONDS))
                )
            self.window_start, self.window_bytes = now, 0

    def speed(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        span = self.samples[-1][0] - self.samples[0][0]
        return sum(b for _, b in self.samples) / span if span > 0 else 0.0


def _open(client: Any, url: str, headers: dict[str, str]) -> Any:
    """GET with redirects followed by hand: each hop must be HTTPS (plain HTTP only in tests), and
    no cookie set by an earlier hop is sent on."""
    import httpx

    for _ in range(MAX_REDIRECTS + 1):
        scheme = urlsplit(url).scheme
        if scheme != "https" and not (ALLOW_HTTP and scheme == "http"):
            raise SourceFailed(
                stored("下载地址不是 HTTPS，已拒绝：{host}", host=urlsplit(url).netloc)
            )
        client.cookies.clear()
        try:
            response = client.send(client.build_request("GET", url, headers=headers), stream=True)
        except httpx.TimeoutException as err:
            raise SourceFailed(stored("连接超时")) from err
        except httpx.HTTPError as err:
            raise SourceFailed(stored("无法连接：{error}", error=type(err).__name__)) from err
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            response.close()
            url = urljoin(url, response.headers["location"])
            continue
        return response
    raise SourceFailed(stored("重定向次数过多"))


def fetch(
    item: Item,
    source: Source,
    part: Path,
    state: dict[str, Any],
    stopping: threading.Event,
    on_progress: Callable[[], None] = lambda: None,
) -> None:
    """Bring part up to the full file from this source, continuing from its current size. Raises
    SourceFailed (try the next source) or Stopped."""
    import httpx

    from jacara import __version__

    expected = item.file.size
    have = part.stat().st_size if part.exists() else 0
    if have > expected:
        part.unlink()
        have = 0
    item.done, item.total = have, expected
    if have == expected:
        return
    headers = {
        "Range": f"bytes={have}-",
        "Accept-Encoding": "identity",
        "User-Agent": f"JacarandaParse/{__version__}",
    }
    known = (state.get("etags") or {}).get(item.file.filename) or {}
    if have and known.get("source") == source.id and known.get("etag"):
        headers["If-Range"] = known["etag"]
    timeout = httpx.Timeout(CONNECT_SECONDS, read=STALL_SECONDS)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = _open(client, source.url, headers)
        try:
            if response.status_code == 206:
                found = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("content-range", "")
                )
                if not found or int(found[1]) != have or found[3] != str(expected):
                    raise SourceFailed(
                        stored(
                            "服务器返回的文件范围或大小与清单不符：{range}",
                            range=response.headers.get("content-range", ""),
                        )
                    )
                mode = "ab"
            elif response.status_code == 200:
                length = response.headers.get("content-length")
                if length is not None and int(length) != expected:
                    raise SourceFailed(stored("服务器上的文件大小与清单不符：{size}", size=length))
                # A 200 answers the whole file: the server ignored Range, or the file changed
                # since (If-Range did not match). Start over.
                have, mode = 0, "wb"
            else:
                raise SourceFailed(stored("服务器返回 HTTP {status}", status=response.status_code))
            etag = response.headers.get("etag")
            if etag:
                etags = dict(state.get("etags") or {})
                etags[item.file.filename] = {"source": source.id, "etag": etag}
                state["etags"] = etags
                write_state(state)
            meter = Meter()
            part.parent.mkdir(parents=True, exist_ok=True)
            item.done = have
            with part.open(mode) as out:
                try:
                    # As received (no chunk size): a chunker would hold back up to a chunk and lose
                    # it when the connection breaks.
                    for block in response.iter_raw():
                        if stopping.is_set():
                            raise Stopped
                        if item.done + len(block) > expected:
                            raise SourceFailed(stored("服务器发送的数据比清单中的文件大"))
                        out.write(block)
                        item.done += len(block)
                        meter.add(len(block))
                        item.speed = meter.speed()
                        on_progress()
                except httpx.TimeoutException as err:
                    raise SourceFailed(stalled()) from err
                except httpx.HTTPError as err:
                    raise SourceFailed(
                        stored("连接中断：{error}", error=type(err).__name__)
                    ) from err
            if item.done != expected:
                raise SourceFailed(stored("连接中断，文件没有下载完整"))
        finally:
            response.close()
            item.speed = 0.0
