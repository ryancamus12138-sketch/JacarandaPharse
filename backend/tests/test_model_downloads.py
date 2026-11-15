"""Model downloads of the desktop app (ADR 0068 section 6), against a local server that behaves like
the real sources (redirects, Range, If-Range, ETag) and can fail in the ways they fail: refusing,
cutting the connection, stalling, sending another file."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from jacara import downloads
from jacara.core.config import get_settings
from jacara.core.owner import PERSONAL_OWNER
from jacara.core.runtime import reset_caches
from jacara.downloads import Item, LockedFile, Source, part_of, read_state
from jacara.downloads.manager import Downloads
from jacara.ops.commands import migrate_to


def gguf(size: int, seed: int = 0) -> bytes:
    """A small file that passes the GGUF header check (magic, version 3)."""
    body = hashlib.sha256(str(seed).encode()).digest() * (size // 32 + 1)
    return (b"GGUF" + struct.pack("<I", 3) + body)[:size]


class Fake(BaseHTTPRequestHandler):
    """routes: path → behaviour. Plain behaviour: answer Range with 206 and the ETag, If-Range
    that does not match with 200 and the whole file."""

    routes: ClassVar[dict[str, dict[str, Any]]] = {}
    seen: ClassVar[list[tuple[str, dict[str, str]]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        del format, args  # Quiet: the tests check requests through seen.

    def do_GET(self) -> None:
        Fake.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        route = Fake.routes.get(self.path, {})
        if "redirect" in route:
            self.send_response(302)
            self.send_header("Location", route["redirect"])
            self.send_header("Set-Cookie", "session=secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "status" in route:
            self.send_response(route["status"])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data: bytes = route["data"]
        etag = route.get("etag", '"v1"')
        start = 0
        wanted = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if wanted and not route.get("ignore_range") and (if_range is None or if_range == etag):
            start = int(wanted.removeprefix("bytes=").split("-")[0])
        if start:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.send_header("ETag", etag)
        self.end_headers()
        sent = start
        step = route.get("step", 64 * 1024)
        while sent < len(data):
            if "cut_at" in route and sent >= route["cut_at"]:
                return  # The connection closes short of the announced length.
            if "stall_at" in route and sent >= route["stall_at"]:
                time.sleep(route.get("stall_for", 3))
                return
            end = min(sent + step, len(data), route.get("cut_at", len(data)))
            try:
                self.wfile.write(data[sent:end])
                self.wfile.flush()
            except OSError:
                return
            sent = end
            if route.get("pace"):
                time.sleep(route["pace"])


@pytest.fixture
def server():
    Fake.routes, Fake.seen = {}, []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name, value in {
        "JACARA_HOME": str(tmp_path),
        "JACARA_DATABASE": "sqlite",
        "JACARA_STORAGE": "local",
        "JACARA_QUEUE": "huey",
        "JACARA_DESKTOP": "true",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(downloads, "ALLOW_HTTP", True)
    monkeypatch.setattr(downloads, "STALL_SECONDS", 1.0)
    monkeypatch.setattr(downloads, "SPACE_MARGIN", 0)
    reset_caches()
    migrate_to(get_settings().database_url, None)
    yield tmp_path
    reset_caches()


MODEL = gguf(700_000, 1)
VISION = gguf(300_000, 2)


def locked(base: str, name: str, data: bytes, slot: str = "model", **routes: dict) -> LockedFile:
    """A listed file at three sources /hf, /ms, /mirror; routes per source override the plain
    behaviour."""
    for source in ("hf", "ms", "mirror"):
        Fake.routes[f"/{source}/{name}"] = {"data": data, **routes.get(source, {})}
    return LockedFile(
        slot=slot,  # type: ignore[arg-type]
        name=name.split(".")[0],
        filename=name,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        license="Apache-2.0",
        base_model="Qwen/Qwen3.5-9B-Base",
        default=True,
        sources=(
            Source("huggingface", f"{base}/hf/{name}"),
            Source("modelscope", f"{base}/ms/{name}"),
            Source("hf_mirror", f"{base}/mirror/{name}"),
        ),
    )


def finish(manager: Downloads, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while manager.running() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not manager.running()


def ranges(path: str) -> list[str | None]:
    return [h.get("range") for p, h in Fake.seen if p == path]


def selected(home: Path) -> dict[str, str]:
    return json.loads((home / "models" / "selected.json").read_text("utf-8"))


def test_downloads_check_import_and_use_the_models(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    vision = locked(server, "mmproj-F16.gguf", VISION, slot="mmproj")
    manager = Downloads((model, vision))
    manager.start([model.filename, vision.filename], PERSONAL_OWNER, None)
    finish(manager)
    items = {i["filename"]: i for i in manager.view()}
    assert {i["state"] for i in items.values()} == {"done"}
    assert items["Model-Q4.gguf"]["imported"] == "Model-Q4@v1"
    assert items["Model-Q4.gguf"]["source"] == "huggingface"
    chosen = selected(home)
    assert chosen["model"].endswith("Model-Q4/v1/Model-Q4.gguf")
    assert chosen["mmproj"].endswith("mmproj-F16/v1/mmproj-F16.gguf")
    assert get_settings().find_model() is not None
    assert not part_of(model).exists()  # Moved into the library, not copied.
    assert read_state()["preferred"] == "huggingface"
    assert ranges("/ms/Model-Q4.gguf") == []  # The other sources were never asked.
    # Asked again: the library already has the same file, nothing is downloaded.
    Fake.seen.clear()
    again = Downloads((model,))
    again.start([model.filename], PERSONAL_OWNER, None)
    finish(again)
    assert again.view()[0]["state"] == "done" and Fake.seen == []


def test_a_failing_source_is_replaced_and_the_next_continues(home, server):
    model = locked(
        server,
        "Model-Q4.gguf",
        MODEL,
        hf={"cut_at": 250_000, "step": 50_000},  # The connection breaks after 250 KB.
        ms={"status": 503},  # The second source is down.
    )
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done" and item["source"] == "hf_mirror"
    assert [s["source"] for s in item["switched"]] == ["huggingface", "modelscope"]
    assert "HTTP 503" in item["switched"][1]["reason"]
    # The mirror was asked only for the rest: nothing downloaded twice.
    assert ranges("/mirror/Model-Q4.gguf") == ["bytes=250000-"]
    assert read_state()["preferred"] == "hf_mirror"
    # The next download asks the source that worked first.
    vision = locked(server, "mmproj-F16.gguf", VISION, slot="mmproj")
    manager = Downloads((vision,))
    manager.start([vision.filename], PERSONAL_OWNER, None)
    finish(manager)
    assert manager.view()[0]["source"] == "hf_mirror"


def test_a_stalled_source_is_given_up(home, server):
    model = locked(
        server, "Model-Q4.gguf", MODEL, hf={"stall_at": 100_000, "stall_for": 3, "step": 50_000}
    )
    manager = Downloads((model,))
    started = time.monotonic()
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done" and item["source"] == "modelscope"
    assert "几乎没有收到数据" in item["switched"][0]["reason"]
    assert time.monotonic() - started < 10
    assert ranges("/ms/Model-Q4.gguf") == ["bytes=100000-"]


def test_pause_keeps_the_part_and_continue_asks_for_the_rest(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL, hf={"step": 8192, "pace": 0.01})
    vision = locked(server, "mmproj-F16.gguf", VISION, slot="mmproj")
    manager = Downloads((model, vision))
    manager.start([model.filename, vision.filename], PERSONAL_OWNER, None)
    deadline = time.monotonic() + 10
    while (manager.view()[0]["done"] < 100_000) and time.monotonic() < deadline:
        time.sleep(0.02)
    manager.pause()
    # The file waiting its turn is paused too: one click continues both.
    assert [i["state"] for i in manager.view()] == ["paused", "paused"]
    have = part_of(model).stat().st_size
    assert 0 < have < len(MODEL)
    # After a restart of the app: shown as paused, continued on a click.
    restarted = Downloads((model,))
    restarted.restore_paused()
    assert restarted.view()[0]["state"] == "paused" and restarted.view()[0]["done"] == have
    Fake.routes["/hf/Model-Q4.gguf"].pop("pace")
    Fake.seen.clear()
    restarted.resume(PERSONAL_OWNER)
    finish(restarted)
    assert restarted.view()[0]["state"] == "done"
    [(_, headers)] = [s for s in Fake.seen if s[0] == "/hf/Model-Q4.gguf"]
    assert headers["range"] == f"bytes={have}-" and headers["if-range"] == '"v1"'


def test_a_changed_file_at_the_source_starts_over(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    part = part_of(model)
    part.parent.mkdir(parents=True)
    part.write_bytes(MODEL[:200_000])
    downloads.write_state({"etags": {model.filename: {"source": "huggingface", "etag": '"old"'}}})
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done"
    # If-Range did not match: the source sent the whole file (200), which replaced the part.
    [(_, headers)] = [s for s in Fake.seen if s[0] == "/hf/Model-Q4.gguf"]
    assert headers["if-range"] == '"old"'


def test_a_damaged_download_is_tried_once_more_then_fails(home, server):
    wrong = gguf(len(MODEL), 99)
    model = locked(server, "Model-Q4.gguf", MODEL)
    for source in ("hf", "ms", "mirror"):
        Fake.routes[f"/{source}/Model-Q4.gguf"]["data"] = wrong
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and "校验不一致" in item["error"]
    assert ranges("/hf/Model-Q4.gguf") == ["bytes=0-", "bytes=0-"]
    assert not part_of(model).exists()
    assert not (home / "models" / "selected.json").exists()


def test_every_source_failing_lists_the_reasons(home, server):
    model = locked(
        server,
        "Model-Q4.gguf",
        MODEL,
        hf={"status": 404},
        ms={"redirect": "ftp://elsewhere/file"},
        mirror={"data": MODEL + b"extra"},
    )
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and item["error"] == "所有下载来源都失败了"
    reasons = [s["reason"] for s in item["switched"]]
    assert "HTTP 404" in reasons[0]
    assert "不是 HTTPS" in reasons[1]
    assert "大小与清单不符" in reasons[2]


def test_redirects_carry_no_cookies(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL, hf={"redirect": f"{server}/cdn/Model-Q4.gguf"})
    Fake.routes["/cdn/Model-Q4.gguf"] = {"data": MODEL}
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    assert manager.view()[0]["state"] == "done"
    cdn = [h for p, h in Fake.seen if p == "/cdn/Model-Q4.gguf"]
    assert cdn and "cookie" not in cdn[0] and cdn[0]["range"] == "bytes=0-"


def test_a_pinned_source_is_the_only_one_asked(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL, ms={"status": 500})
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, "modelscope")
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and [s["source"] for s in item["switched"]] == ["modelscope"]
    assert ranges("/hf/Model-Q4.gguf") == []


def test_not_enough_disk_space(home, server, monkeypatch):
    model = locked(server, "Model-Q4.gguf", MODEL)
    usage = type("usage", (), {"free": 10, "total": 10, "used": 0})
    monkeypatch.setattr(downloads.shutil, "disk_usage", lambda _p: usage)
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and "磁盘空间不足" in item["error"]
    assert Fake.seen == []


def test_files_placed_in_the_inbox_are_checked_and_imported(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    vision = locked(server, "mmproj-F16.gguf", VISION, slot="mmproj")
    manager = Downloads((model, vision))
    inbox = home / "models" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "Model-Q4.gguf").write_bytes(MODEL[:1000])  # Still being copied.
    (inbox / "mmproj-F16.gguf").write_bytes(gguf(len(VISION), 7))  # Another file of the same size.
    (inbox / "Other.gguf").write_bytes(b"GGUF")
    old = time.time() - 5
    first = {f["name"]: f["status"] for f in manager.scan_inbox(PERSONAL_OWNER)}
    assert first == {
        "inbox/Model-Q4.gguf": "copying",
        "inbox/mmproj-F16.gguf": "copying",
        "inbox/Other.gguf": "copying",
    }
    (inbox / "Model-Q4.gguf").write_bytes(MODEL)
    os.utime(inbox / "Model-Q4.gguf", (old, old))
    second = {f["name"]: f["status"] for f in manager.scan_inbox(PERSONAL_OWNER)}
    assert second["inbox/Model-Q4.gguf"] == "copying"  # Changed since the last check.
    assert second["inbox/mmproj-F16.gguf"] == "waiting"  # Unchanged: checked now.
    assert second["inbox/Other.gguf"] == "unknown"  # Not listed: imported when asked.
    finish(manager)
    third = {f["name"]: f["status"] for f in manager.scan_inbox(PERSONAL_OWNER)}
    assert third["inbox/Model-Q4.gguf"] == "waiting" and third["inbox/mmproj-F16.gguf"] == "failed"
    finish(manager)
    items = {i["filename"]: i for i in manager.view()}
    assert items["Model-Q4.gguf"]["state"] == "done"
    assert items["mmproj-F16.gguf"]["state"] == "failed"
    assert "内容不同" in items["mmproj-F16.gguf"]["error"]
    assert not (inbox / "Model-Q4.gguf").exists()  # Moved into the library.
    assert (inbox / "mmproj-F16.gguf").exists()  # A file that failed stays where it was.
    assert Fake.seen == []  # Nothing from the network.


def test_inbox_links_are_copied_and_imported_files_are_not_imported_again(home, server, tmp_path):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    # Not "Models": on macOS and Windows file names ignore case, so that would be the models folder
    # of the data folder (tmp_path) itself.
    elsewhere = tmp_path / "Downloaded"
    elsewhere.mkdir()
    (elsewhere / "Model-Q4.gguf").write_bytes(MODEL)
    inbox = home / "models" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "Model-Q4.gguf").symlink_to(elsewhere / "Model-Q4.gguf")
    assert manager.scan_inbox(PERSONAL_OWNER)[0]["status"] == "copying"
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "waiting"
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done" and item["origin"] == "inbox"
    stored_file = Path(home, selected(home)["model"])
    assert stored_file.is_file() and not stored_file.is_symlink()  # A copy, not the link.
    assert (elsewhere / "Model-Q4.gguf").read_bytes() == MODEL  # The original stays.
    assert not list((home / "models" / "downloads").glob("*.copy"))
    # The link is still in the inbox: shown as already in the library, and not imported again.
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "in_library" and row["done"] is None
    assert not manager.running() and len(manager.view()) == 1
    # A fresh process (after a restart) knows it from the model library too.
    again = Downloads((model,))
    again.scan_inbox(PERSONAL_OWNER)
    assert again.scan_inbox(PERSONAL_OWNER)[0]["status"] == "in_library"
    assert again.view() == []


def test_inbox_rows_follow_the_file_being_worked_on(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL, hf={"step": 8192, "pace": 0.02})
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    inbox = home / "models" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "Model-Q4.gguf").write_bytes(MODEL)
    manager.scan_inbox(PERSONAL_OWNER)
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "busy"  # Being downloaded: the inbox file waits.
    manager.cancel()
    # While it is worked on, the row shows the item's step and progress.
    path = inbox / "Model-Q4.gguf"
    manager.items[model.filename] = Item(
        model,
        origin="inbox",
        path=path,
        rel="inbox/Model-Q4.gguf",
        state="verifying",
        done=1000,
        total=len(MODEL),
    )
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "verifying" and row["done"] == 1000 and row["total"] == len(MODEL)
    manager.items[model.filename].state = "downloading"  # Copying a link.
    assert manager.scan_inbox(PERSONAL_OWNER)[0]["status"] == "importing"
    assert not manager.running()


def scan_twice(manager: Downloads) -> dict[str, dict[str, Any]]:
    """Two checks: the second sees the files unchanged (complete)."""
    manager.scan_inbox(PERSONAL_OWNER)
    return {r["name"]: r for r in manager.scan_inbox(PERSONAL_OWNER)}


def test_a_downloaded_folder_is_searched_at_any_depth(home, server):
    """A Hugging Face cache placed in the inbox: blobs/ holds the file under its hash,
    snapshots/<commit>/ a link with its real name. The file is recognized by content and imported
    once, under its listed name."""
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    repo = home / "models" / "inbox" / "hub" / "models--org--Model-GGUF"
    blob = repo / "blobs" / hashlib.sha256(MODEL).hexdigest()
    blob.parent.mkdir(parents=True)
    blob.write_bytes(MODEL)
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "Model-Q4.gguf").symlink_to(Path("..", "..", "blobs", blob.name))
    (snapshot / "README.md").write_text("card")
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("abc123")
    rows = scan_twice(manager)
    base = "inbox/hub/models--org--Model-GGUF"
    assert rows[f"{base}/blobs/{blob.name}"]["status"] == "waiting"  # Same size: checked.
    assert rows[f"{base}/snapshots/abc123/Model-Q4.gguf"]["status"] == "busy"  # Taken once.
    assert manager.inbox_other == 2  # README.md, refs/main.
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done" and item["imported"] == "Model-Q4@v1"
    assert selected(home)["model"] == "models/hub/Model-Q4/v1/Model-Q4.gguf"
    assert not blob.exists()  # Moved into the library; the link left behind points nowhere.
    assert {r["name"] for r in manager.scan_inbox(PERSONAL_OWNER)} == set()


def test_renamed_files_of_the_listed_size_are_checked_by_content(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    other = home / "models" / "inbox" / "a" / "b" / "renamed.gguf"
    other.parent.mkdir(parents=True)
    other.write_bytes(gguf(len(MODEL), 9))  # Same size, other content.
    assert scan_twice(manager)["inbox/a/b/renamed.gguf"]["status"] == "waiting"
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and "不是这个文件" in item["error"]
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    # Not the listed file: says why, and offers to import it as it is.
    assert row["status"] == "failed" and row["importable"] and row["kind"] == "model"
    assert "不是这个文件" in row["error"]
    assert other.exists()


def test_browser_downloads_and_links_to_folders_are_left_alone(home, server, tmp_path):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    inbox = home / "models" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "Model-Q4.gguf.crdownload").write_bytes(MODEL)
    (inbox / "Big.gguf.download").mkdir()  # Safari's package.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Model-Q4.gguf").write_bytes(MODEL)
    (inbox / "linked").symlink_to(elsewhere, target_is_directory=True)
    (inbox / "loop").mkdir()
    (inbox / "loop" / "up").symlink_to(inbox, target_is_directory=True)
    (inbox / ".hidden.gguf").write_bytes(MODEL)
    rows = scan_twice(manager)
    assert {k: r["status"] for k, r in rows.items()} == {
        "inbox/Model-Q4.gguf.crdownload": "copying",
        "inbox/Big.gguf.download": "copying",
        "inbox/linked": "folder_link",
        "inbox/loop/up": "folder_link",
    }
    assert not manager.running() and manager.view() == []


def test_model_files_not_in_the_list_are_imported_when_asked(home, server):
    manager = Downloads(())
    inbox = home / "models" / "inbox"
    mine = inbox / "downloads" / "My Model.gguf"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(gguf(4000, 1))
    vision = inbox / "downloads" / "mmproj-mine.gguf"
    vision.write_bytes(gguf(3000, 2))
    rows = scan_twice(manager)
    row = rows["inbox/downloads/My Model.gguf"]
    assert row["status"] == "unknown" and row["kind"] == "model" and row["suggested"] == "My-Model"
    assert rows["inbox/downloads/mmproj-mine.gguf"]["kind"] == "mmproj"
    assert not manager.running()  # Nothing without a click.
    manager.import_inbox("inbox/downloads/My Model.gguf", "model", "My-Model", None, PERSONAL_OWNER)
    finish(manager)
    item = next(i for i in manager.view() if i["rel"] == "inbox/downloads/My Model.gguf")
    assert item["state"] == "done" and item["imported"] == "My-Model@v1" and item["custom"]
    assert selected(home)["model"] == "models/hub/My-Model/v1/My Model.gguf"
    assert not mine.exists()  # Moved into the library.
    for bad in ("../jacara.db", "/etc/hosts", "data/x.gguf", "inbox/missing.gguf"):
        with pytest.raises(downloads.DownloadError):
            manager.import_inbox(bad, "model", "x", None, PERSONAL_OWNER)
    with pytest.raises(downloads.DownloadError):
        manager.import_inbox(
            "inbox/downloads/mmproj-mine.gguf", "ocr_det", "x", None, PERSONAL_OWNER
        )
    with pytest.raises(downloads.DownloadError):  # The library's name rules.
        manager.import_inbox(
            "inbox/downloads/mmproj-mine.gguf", "mmproj", "a/b", None, PERSONAL_OWNER
        )


def test_a_file_the_library_refuses_stays_in_the_inbox(home, server):
    manager = Downloads(())
    bad = home / "models" / "inbox" / "broken.gguf"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a gguf file at all")
    scan_twice(manager)
    manager.import_inbox("inbox/broken.gguf", "model", "broken", None, PERSONAL_OWNER)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "failed" and "GGUF" in item["error"]
    assert bad.read_bytes() == b"not a gguf file at all"
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "failed" and row["importable"] and "GGUF" in row["error"]


def test_split_models_are_imported_with_all_their_files(home, server):
    manager = Downloads(())
    folder = home / "models" / "inbox" / "Big-Q4"
    folder.mkdir(parents=True)
    first = folder / "Big-Q4-00001-of-00002.gguf"
    first.write_bytes(gguf(5000, 1))
    assert scan_twice(manager)["inbox/Big-Q4/Big-Q4-00001-of-00002.gguf"]["status"] == (
        "shards_missing"
    )
    with pytest.raises(downloads.DownloadError):
        manager.import_inbox(
            first.relative_to(home / "models").as_posix(), "model", "Big", None, PERSONAL_OWNER
        )
    (folder / "Big-Q4-00002-of-00002.gguf").write_bytes(gguf(4000, 2))
    row = scan_twice(manager)["inbox/Big-Q4/Big-Q4-00001-of-00002.gguf"]
    assert row["status"] == "unknown" and (row["parts"], row["count"]) == (2, 2)
    assert row["size"] == 9000 and row["suggested"] == "Big-Q4"
    manager.import_inbox(row["name"], "model", "Big-Q4", None, PERSONAL_OWNER)
    finish(manager)
    version = home / "models" / "hub" / "Big-Q4" / "v1"
    assert sorted(p.name for p in version.glob("*.gguf")) == [
        "Big-Q4-00001-of-00002.gguf",
        "Big-Q4-00002-of-00002.gguf",
    ]
    from jacara.modules.hub import registry

    [stored_version] = registry.versions("Big-Q4")
    assert [f.role for f in stored_version.files] == ["weights", "shard"]
    assert selected(home)["model"].endswith("Big-Q4-00001-of-00002.gguf")
    assert not folder.exists()  # Emptied by the import: removed.


RAPID_MODELS = Path(__import__("rapidocr").__file__).parent / "models"


@pytest.mark.skipif(not (RAPID_MODELS / "PP-OCRv6_det_small.onnx").exists(), reason="没有自带模型")
def test_onnx_models_are_offered_with_a_suggested_kind_and_not_switched_to(home, server):
    import shutil

    manager = Downloads(())
    folder = home / "models" / "inbox" / "ocr"
    folder.mkdir(parents=True)
    shutil.copyfile(RAPID_MODELS / "PP-OCRv6_det_small.onnx", folder / "PP-OCRv6_det_small.onnx")
    (folder / "keys.txt").write_text("a\nb\n")
    (folder / "slanet-plus.onnx").write_bytes(b"x")
    rows = scan_twice(manager)
    det = rows["inbox/ocr/PP-OCRv6_det_small.onnx"]
    assert det["kind"] == "ocr_det" and det["dictionaries"] == ["inbox/ocr/keys.txt"]
    assert rows["inbox/ocr/slanet-plus.onnx"]["kind"] == "table"
    with pytest.raises(downloads.DownloadError):  # Only recognition models take a dictionary.
        manager.import_inbox(det["name"], "ocr_det", "det", "inbox/ocr/keys.txt", PERSONAL_OWNER)
    manager.import_inbox(det["name"], "ocr_det", "PP-OCRv6-det", None, PERSONAL_OWNER)
    finish(manager)
    [item] = manager.view()
    assert item["state"] == "done" and item["slot"] == "ocr_det"
    assert "ocr_det" not in selected_or_empty(home)  # Switched to on the settings page.


def selected_or_empty(home: Path) -> dict[str, str]:
    path = home / "models" / "selected.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def test_loose_files_in_the_models_folder_are_not_run_but_offered(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    loose = home / "models" / "Model-Q4.gguf"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(MODEL)
    get_settings.cache_clear()
    assert get_settings().find_model() is None  # The desktop app runs library models only.
    row = scan_twice(manager)["Model-Q4.gguf"]
    assert row["status"] == "unknown" and row["kind"] == "model" and row["expected"] is None
    assert not manager.running()
    manager.import_inbox("Model-Q4.gguf", "model", "ignored", None, PERSONAL_OWNER)
    finish(manager)
    [item] = manager.view()
    assert item["imported"] == "Model-Q4@v1" and not item["custom"]  # Checked against the list.
    assert not loose.exists()
    assert get_settings().find_model() == home / "models/hub/Model-Q4/v1/Model-Q4.gguf"


def test_files_in_the_list_can_be_deleted_and_empty_folders_go_too(home, server, tmp_path):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    inbox = home / "models" / "inbox"
    deep = inbox / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "Other.gguf").write_bytes(gguf(100))
    elsewhere = tmp_path / "keep.gguf"
    elsewhere.write_bytes(gguf(200))
    (inbox / "link.gguf").symlink_to(elsewhere)
    (inbox / "folder-link").symlink_to(tmp_path, target_is_directory=True)
    split = inbox / "Big"
    split.mkdir()
    for n in (1, 2):
        (split / f"Big-0000{n}-of-00003.gguf").write_bytes(gguf(100, n))
    (inbox / "Safari.gguf.download").mkdir()
    (deep / ".DS_Store").write_bytes(b"Finder")  # Written by the file manager: does not count.
    (inbox / "a" / "Thumbs.db").write_bytes(b"Explorer")
    keep = inbox / "kept"
    keep.mkdir()
    (keep / "x.gguf").write_bytes(gguf(100))
    (keep / "notes.txt").write_text("mine")
    rows = scan_twice(manager)
    assert rows["inbox/link.gguf"]["link"] and rows["inbox/link.gguf"]["deletable"]
    assert rows["inbox/link.gguf"]["target"] == str(elsewhere.resolve())
    assert rows["inbox/a/b/Other.gguf"]["target"] is None
    assert not rows["inbox/Safari.gguf.download"]["deletable"]
    deleted = manager.delete_inbox(
        [
            "inbox/a/b/Other.gguf",
            "inbox/link.gguf",
            "inbox/folder-link",
            "inbox/Big/Big-00001-of-00003.gguf",
        ],
        PERSONAL_OWNER,
    )
    assert sorted(deleted) == [
        "inbox/Big/Big-00001-of-00003.gguf",
        "inbox/Big/Big-00002-of-00003.gguf",
        "inbox/a/b/Other.gguf",
        "inbox/folder-link",
        "inbox/link.gguf",
    ]
    assert elsewhere.exists() and tmp_path.is_dir()  # Only the links went.
    assert not (inbox / "a").exists() and not split.exists()  # Emptied folders are removed.
    manager.delete_inbox(["inbox/kept/x.gguf"], PERSONAL_OWNER)
    assert (keep / "notes.txt").exists()  # A folder with other files in it stays.
    assert inbox.is_dir()
    for bad in ("../jacara.db", "inbox/missing.gguf", "inbox/Safari.gguf.download", "data/x"):
        with pytest.raises(downloads.DownloadError):
            manager.delete_inbox([bad], PERSONAL_OWNER)
    actions = audit_actions()
    assert "hub:inbox_delete" in actions


def audit_actions() -> list[str]:
    from sqlalchemy import select

    from jacara.core.db import session_scope
    from jacara.models import AuditLog

    with session_scope() as session:
        return list(session.scalars(select(AuditLog.action)))


def test_a_file_being_worked_on_is_not_deleted(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    path = home / "models" / "inbox" / "Model-Q4.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(MODEL)
    manager.items[model.filename] = Item(
        model, origin="inbox", path=path, rel="inbox/Model-Q4.gguf", state="verifying"
    )
    rows = scan_twice(manager)
    assert not rows["inbox/Model-Q4.gguf"]["deletable"]
    with pytest.raises(downloads.DownloadError):
        manager.delete_inbox(["inbox/Model-Q4.gguf"], PERSONAL_OWNER)
    assert path.exists()


def test_old_model_folders_are_offered_and_the_settings_list_each_model_once(
    home, server, tmp_path, monkeypatch
):
    """Where earlier versions kept models (models/table/, models/ocr/) the files are offered for
    import, not run; the settings list the default and the imported versions, an imported copy of
    the default only once."""
    from fastapi.testclient import TestClient

    from jacara.app import create_app
    from jacara.core import config
    from jacara.modules.hub import registry
    from jacara.modules.model_admin import service

    program = tmp_path / "program"
    shipped = program / "models" / "table" / "slanet-plus.onnx"
    shipped.parent.mkdir(parents=True)
    shipped.write_bytes(b"table model")
    monkeypatch.setattr(config, "resource_dir", lambda: program)
    monkeypatch.setattr(service, "model_props", lambda _s: {})
    loose = home / "models" / "table" / "slanet-plus.onnx"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"older table model")
    (home / "models" / "ocr").mkdir()
    (home / "models" / "ocr" / "my_det.onnx").write_bytes(b"det")
    get_settings.cache_clear()
    assert get_settings().table_model() == shipped  # The loose file is not used.
    rows = scan_twice(Downloads(()))
    assert rows["table/slanet-plus.onnx"]["kind"] == "table"
    assert rows["table/slanet-plus.onnx"]["importable"]
    assert rows["ocr/my_det.onnx"]["kind"] == "ocr_det" and rows["ocr/my_det.onnx"]["legacy"]
    monkeypatch.setattr(registry, "check", lambda *_a: {"format": "onnx"})
    for name, data in (("slanet-copy", b"table model"), ("other", b"another table model")):
        work = registry.incoming()
        (work / "t.onnx").write_bytes(data)
        registry.add_version(name, "table", work, "t.onnx", None, {}, "")
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        slots = {s["key"]: s for s in client.get("/api/v1/models").json()["slots"]}
    table = slots["table"]
    assert table["source"] == "bundled"
    assert [f["name"] for f in table["files"]] == ["SLANet-plus", "other v1"]
    assert [f["name"] for f in slots["model"]["files"]] == []  # No loose files.


def test_the_model_server_is_not_started_with_a_file_that_is_gone(home, monkeypatch):
    from jacara import desktop
    from jacara.plugins.model_backends import server as model_server

    model = home / "models" / "hub" / "M" / "v1" / "M.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(gguf(100))
    (home / "models" / "selected.json").write_text(
        json.dumps({"model": "models/hub/M/v1/M.gguf"}), "utf-8"
    )
    get_settings.cache_clear()
    monkeypatch.setattr(
        model_server, "server_command", lambda m, port, mmproj: ["llama-server", "-m", str(m)]
    )
    [child] = [c for c in desktop.Services(1, "key").children() if c.name == "model"]
    assert child.build is not None
    built = child.build()  # Looked up again before every start.
    assert isinstance(built, tuple) and built[0] == ["llama-server", "-m", str(model)]
    model.unlink()
    child.start()
    assert child.process is None and child.given_up
    assert "model file not found" in (home / "logs" / "model.log").read_text("utf-8")


def test_a_chosen_file_is_copied_checked_and_used(home, server, tmp_path):
    model = locked(server, "Model-Q4.gguf", MODEL)
    elsewhere = tmp_path / "Downloads"
    elsewhere.mkdir()
    (elsewhere / "Model-Q4.gguf").write_bytes(MODEL)
    (elsewhere / "My-Model.gguf").write_bytes(gguf(5000, 3))
    manager = Downloads((model,))
    manager.add_file(elsewhere / "Model-Q4.gguf", PERSONAL_OWNER)
    finish(manager)
    manager.add_file(elsewhere / "My-Model.gguf", PERSONAL_OWNER)
    finish(manager)
    items = {i["filename"]: i for i in manager.view()}
    assert items["Model-Q4.gguf"]["state"] == "done" and not items["Model-Q4.gguf"]["custom"]
    assert items["My-Model.gguf"]["state"] == "done" and items["My-Model.gguf"]["custom"]
    assert selected(home)["model"].endswith("My-Model/v1/My-Model.gguf")
    assert (elsewhere / "Model-Q4.gguf").exists()  # The original stays.
    with pytest.raises(downloads.DownloadError):
        manager.add_file(elsewhere / "missing.gguf", PERSONAL_OWNER)


def test_cancel_removes_the_part(home, server):
    model = locked(server, "Model-Q4.gguf", MODEL, hf={"step": 8192, "pace": 0.02})
    manager = Downloads((model,))
    manager.start([model.filename], PERSONAL_OWNER, None)
    deadline = time.monotonic() + 10
    while not part_of(model).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    manager.cancel()
    assert manager.view() == [] and not part_of(model).exists()


def test_the_shipped_list_is_well_formed():
    listed = downloads.load_lock()
    assert [f.filename for f in listed] == ["Qwen3.5-9B-Q4_K_M.gguf", "mmproj-F16.gguf"]
    for file in listed:
        assert [s.id for s in file.sources] == ["huggingface", "modelscope", "hf_mirror"]
        assert all(s.url.startswith("https://") and "/resolve/" in s.url for s in file.sources)
        assert all("/main/" not in s.url and "/master/" not in s.url for s in file.sources)
    assert listed[0].size == 5_680_522_464 and listed[1].size == 918_166_080
    with pytest.raises(downloads.LockError):
        downloads.parse_lock(
            'version = 1\n[[files]]\nslot = "model"\nname = "x"\nfilename = "x.gguf"\n'
            'size = 1\nsha256 = "00"\nlicense = ""\n'
            '[[files.sources]]\nid = "huggingface"\nurl = "http://x/x.gguf"\n'
        )


def test_the_wizard_api(home, server, monkeypatch):
    from fastapi.testclient import TestClient

    from jacara.app import create_app
    from jacara.modules.setup import router

    model = locked(server, "Model-Q4.gguf", MODEL)
    manager = Downloads((model,))
    monkeypatch.setattr(router, "DOWNLOADS", manager)
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        state = client.get("/api/v1/setup").json()
        assert state["needs_model"] is True and state["files"][0]["in_library"] is False
        assert [s["id"] for s in state["sources"]] == ["huggingface", "modelscope", "hf_mirror"]
        assert (
            client.post("/api/v1/setup/downloads", json={"files": ["nope.gguf"]}).status_code == 400
        )
        started = client.post("/api/v1/setup/downloads", json={"files": [model.filename]})
        assert started.status_code == 202
        finish(manager)
        state = client.get("/api/v1/setup").json()
        assert state["needs_model"] is False and state["model"] == "Model-Q4.gguf"
        assert state["items"][0]["state"] == "done" and state["files"][0]["in_library"] is True
        actions = [x["action"] for x in client.get("/api/v1/audit-logs").json()["items"]]
        assert "hub:model_download" in actions and "hub:model_use" in actions


def test_the_wizard_reports_a_missing_jacara_model_and_checks_the_chosen_file(
    home, server, monkeypatch, tmp_path
):
    import subprocess

    from fastapi.testclient import TestClient

    from jacara.app import create_app
    from jacara.modules.setup import router

    monkeypatch.setattr(router, "DOWNLOADS", Downloads((locked(server, "Model-Q4.gguf", MODEL),)))
    monkeypatch.setenv("JACARA_MODEL", "models/gone.gguf")
    get_settings.cache_clear()
    chosen = tmp_path / "slanet-plus.onnx"

    def dialog(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=f"{chosen}\n".encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", dialog)
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        state = client.get("/api/v1/setup").json()
        assert state["needs_model"] is True and state["model"] is None
        assert state["model_missing"] == str(home / "models" / "gone.gguf")
        refused = client.post("/api/v1/setup/choose-file")
        assert refused.status_code == 400 and ".gguf" in refused.json()["detail"]
        chosen = tmp_path / "Model.GGUF"
        assert client.post("/api/v1/setup/choose-file").json() == {"path": str(chosen)}


def test_the_wizard_is_only_in_the_desktop_app(home, monkeypatch):
    from fastapi.testclient import TestClient

    from jacara.app import create_app

    monkeypatch.setenv("JACARA_DESKTOP", "false")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/setup").status_code == 404
        assert client.post("/api/v1/setup/downloads", json={"files": ["x"]}).status_code == 404
