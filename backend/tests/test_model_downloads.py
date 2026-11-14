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
        "Model-Q4.gguf": "copying",
        "mmproj-F16.gguf": "copying",
        "Other.gguf": "unknown",
    }
    (inbox / "Model-Q4.gguf").write_bytes(MODEL)
    os.utime(inbox / "Model-Q4.gguf", (old, old))
    second = {f["name"]: f["status"] for f in manager.scan_inbox(PERSONAL_OWNER)}
    assert second["Model-Q4.gguf"] == "copying"  # Changed since the last check.
    assert second["mmproj-F16.gguf"] == "waiting"  # Unchanged: checked now.
    finish(manager)
    third = {f["name"]: f["status"] for f in manager.scan_inbox(PERSONAL_OWNER)}
    assert third["Model-Q4.gguf"] == "waiting" and third["mmproj-F16.gguf"] == "failed"
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
    elsewhere = tmp_path / "Models"
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
        model, origin="inbox", path=path, state="verifying", done=1000, total=len(MODEL)
    )
    [row] = manager.scan_inbox(PERSONAL_OWNER)
    assert row["status"] == "verifying" and row["done"] == 1000 and row["total"] == len(MODEL)
    manager.items[model.filename].state = "downloading"  # Copying a link.
    assert manager.scan_inbox(PERSONAL_OWNER)[0]["status"] == "importing"
    assert not manager.running()


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


def test_the_wizard_is_only_in_the_desktop_app(home, monkeypatch):
    from fastapi.testclient import TestClient

    from jacara.app import create_app

    monkeypatch.setenv("JACARA_DESKTOP", "false")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/setup").status_code == 404
        assert client.post("/api/v1/setup/downloads", json={"files": ["x"]}).status_code == 404
