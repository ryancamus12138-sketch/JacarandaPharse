"""Model and dataset library, and OCR model evaluation.

Model files are placed in models/hub/ in a temporary folder; ONNX checks use the models bundled
with the RapidOCR package (installed with the dependencies; no network required)."""

from __future__ import annotations

import io
import json
import struct
import uuid
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from jacara.core import config
from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.eval.ocr import (
    EvalArm,
    evaluate_image,
    levenshtein,
    normalized_distance,
)
from jacara.models import AuditLog, Dataset, OcrEvalRun
from jacara.modules.hub import datasets as dataset_files
from jacara.modules.hub import registry
from jacara.modules.hub.datasets import Box
from jacara.modules.hub.evals import run_eval
from jacara.modules.hub.registry import HubError
from jacara.modules.model_admin import service as model_admin

from .conftest import raises

RAPID_MODELS = Path(__import__("rapidocr").__file__).parent / "models"


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(model_admin, "model_props", lambda _s: {})
    for name in ("JACARA_MODEL", "JACARA_OCR_REC", "JACARA_OCR_DET", "JACARA_OCR_DICT"):
        monkeypatch.delenv(name, raising=False)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def gguf(version: int = 3) -> bytes:
    return b"GGUF" + struct.pack("<I", version) + b"\0" * 64


def upload(client, files: dict[str, bytes], **finish) -> dict:
    started = client.post("/api/v1/hub/models/uploads")
    assert started.status_code == 201, started.text
    upload_id = started.json()["upload_id"]
    for name, data in files.items():
        sent = client.put(
            f"/api/v1/hub/models/uploads/{upload_id}/files/{name}",
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert sent.status_code == 200, sent.text
    return client.post(f"/api/v1/hub/models/uploads/{upload_id}/finish", json=finish).json() | {
        "_upload": upload_id
    }


# ---- File validation ----


def test_gguf_header_is_checked(tmp_path):
    good = tmp_path / "a.gguf"
    good.write_bytes(gguf(3))
    assert registry.check_gguf(good) == {"format": "gguf", "gguf_version": 3}
    bad = tmp_path / "b.gguf"
    bad.write_bytes(b"PK\x03\x04" + b"\0" * 10)
    with raises(HubError, "不是 GGUF"):
        registry.check_gguf(bad)


@pytest.mark.skipif(not (RAPID_MODELS / "PP-OCRv6_rec_small.onnx").exists(), reason="没有自带模型")
def test_onnx_rec_and_det_are_told_apart(tmp_path):
    rec = RAPID_MODELS / "PP-OCRv6_rec_small.onnx"
    det = RAPID_MODELS / "PP-OCRv6_det_small.onnx"
    info = registry.check_onnx(rec, "ocr_rec", None)
    # Embedded character list: class count = characters + 2 (RapidOCR adds blank and space).
    assert info["dict_source"] == "model" and info["classes"] == info["characters"] + 2
    with raises(HubError, "应为检测模型"):
        registry.check_onnx(det, "ocr_rec", None)
    with raises(HubError, "应为识别模型"):
        registry.check_onnx(rec, "ocr_det", None)
    assert registry.check_onnx(det, "ocr_det", None)["format"] == "onnx"
    bad = tmp_path / "x.onnx"
    bad.write_bytes(b"not a model")
    with raises(HubError, "onnxruntime 无法打开"):
        registry.check_onnx(bad, "ocr_det", None)


def test_model_card_round_trip(tmp_path):
    card = tmp_path / "README.md"
    registry.write_card(card, {"kind": "ocr_rec", "license": "apache-2.0"}, "微调的收据识别\n")
    assert registry.read_card(card) == (
        {"kind": "ocr_rec", "license": "apache-2.0"},
        "微调的收据识别\n",
    )


# ---- Model library API ----


def test_upload_check_version_use_and_delete(client, factory, root):
    first = upload(
        client,
        {"Qwen-test.gguf": gguf()},
        name="qwen",
        kind="model",
        weights="Qwen-test.gguf",
        license="apache-2.0",
        description="测试用",
    )
    assert first["version"] == 1 and first["path"] == "models/hub/qwen/v1/Qwen-test.gguf"
    assert first["files"][0]["role"] == "weights" and len(first["files"][0]["sha256"]) == 64
    second = upload(
        client, {"Qwen-test.gguf": gguf(2)}, name="qwen", kind="model", weights="Qwen-test.gguf"
    )
    assert second["version"] == 2
    assert not (root / "models" / "hub" / ".incoming" / second["_upload"]).exists()

    listed = client.get("/api/v1/hub/models").json()
    assert [v["version"] for v in listed["models"][0]["versions"]] == [2, 1]
    # Library versions are selectable on the model selection page too.
    names = [f["name"] for f in client.get("/api/v1/models").json()["slots"][0]["files"]]
    assert "qwen v1（模型库）" in names

    used = client.post("/api/v1/hub/models/qwen/versions/1/use")
    assert used.status_code == 200, used.text
    saved = json.loads((root / "models" / "selected.json").read_text())
    assert saved["model"] == "models/hub/qwen/v1/Qwen-test.gguf"
    versions = used.json()["models"][0]["versions"]
    assert [v["in_use"] for v in versions] == [False, True]

    refused = client.delete("/api/v1/hub/models/qwen/versions/1")
    assert refused.status_code == 400 and "正在使用" in refused.json()["detail"]
    assert client.delete("/api/v1/hub/models/qwen/versions/2").status_code == 204
    with session_scope(factory) as session:
        actions = [log.action for log in session.query(AuditLog).order_by(AuditLog.created_at)]
    assert actions.count("hub:model_import") == 2
    assert "hub:model_use" in actions and "hub:model_delete" in actions


def test_wrong_files_are_refused_and_nothing_is_left(client, root):
    bad = upload(client, {"model.gguf": b"nope" * 10}, name="m", kind="model", weights="model.gguf")
    assert "不是 GGUF" in bad["detail"]
    assert registry.versions() == []
    assert not any((root / "models" / "hub" / ".incoming").iterdir())
    started = client.post("/api/v1/hub/models/uploads").json()["upload_id"]
    response = client.put(f"/api/v1/hub/models/uploads/{started}/files/run.sh", content=b"x")
    assert response.status_code == 400
    response = client.put(f"/api/v1/hub/models/uploads/{started}/files/..%2Fa.gguf", content=b"x")
    assert response.status_code != 200
    bad_name = upload(client, {"a.gguf": gguf()}, name="../evil", kind="model", weights="a.gguf")
    assert "模型名" in bad_name["detail"]


def test_exported_version_imports_on_another_machine(client, root):
    upload(client, {"t.gguf": gguf()}, name="tiny", kind="model", weights="t.gguf", license="mit")
    zipped = client.get("/api/v1/hub/models/tiny/versions/1/download")
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert sorted(archive.namelist()) == ["README.md", "t.gguf"]
    again = upload(client, {"tiny-v1.zip": zipped.content}, name="tiny", archive="tiny-v1.zip")
    assert again["version"] == 2 and again["license"] == "mit" and again["kind"] == "model"
    assert again["files"][0]["sha256"] == registry.get_version("tiny", 1).files[0].sha256


def test_members_cannot_change_models(client, root, monkeypatch):
    from jacara.core import deps
    from jacara.core.owner import Owner

    monkeypatch.setenv("JACARA_MODE", "team")  # Permission checks always pass in personal mode.
    config.get_settings.cache_clear()
    member = Owner(uuid.uuid4(), uuid.uuid4(), role="member")
    client.app.dependency_overrides[deps.get_owner] = lambda: member
    try:
        assert client.get("/api/v1/hub/models").status_code == 200
        assert client.post("/api/v1/hub/models/uploads").status_code == 403
    finally:
        client.app.dependency_overrides.pop(deps.get_owner)


def test_command_line_import(root, tmp_path):
    from jacara.modules.hub.service import import_files

    source = tmp_path / "from-usb" / "big.gguf"
    source.parent.mkdir()
    source.write_bytes(gguf())
    version = import_files("usb-model", "model", source, None, {"imported_by": "命令行"})
    assert (version.name, version.version) == ("usb-model", 1)
    assert source.exists()  # Copied; the original file is untouched.
    keys = tmp_path / "from-usb" / "keys.txt"
    keys.write_text("a\nb\n")
    with raises(HubError, "仅识别模型需要字典"):
        import_files("usb-model", "model", source, keys, {})
    with raises(HubError, "新版本需为同一种类"):
        import_files("usb-model", "mmproj", source, None, {})


# ---- Datasets ----


def png(color: str = "white", size: tuple[int, int] = (40, 12)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def rec_zip(extra: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for n in range(1, 5):
            archive.writestr(f"crop_img/s_{n:04d}.png", png())
        archive.writestr(
            "rec_gt_train.txt", "crop_img/s_0001.png\tTOTAL\ncrop_img/s_0002.png\t9.00\n"
        )
        archive.writestr(
            "rec_gt_val.txt",
            "crop_img/s_0003.png\t25/12/2018\ncrop_img/s_0004.png\tCASH\n"
            "missing.png\tX\nbad line\n",
        )
        # Ignored when split files exist.
        archive.writestr("rec_gt.txt", "crop_img/s_0001.png\tTOTAL\n")
        manifest = [
            {"image": "crop_img/s_0003.png", "document_group": "g_a"},
            {"image": "crop_img/s_0004.png", "document_group": "g_b"},
        ]
        archive.writestr("manifest.jsonl", "".join(json.dumps(m) + "\n" for m in manifest))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


def test_rec_dataset_is_parsed_with_splits_groups_and_problems():
    parsed = dataset_files.parse(rec_zip(), "ocr_rec")
    assert parsed.counts == {"train": 2, "val": 2, "test": 0, "all": 0}
    assert parsed.groups == {"val": 2}
    assert {render(k): n for k, n in parsed.problems.items()} == {
        "zip 中缺少该图片": 1,
        "未使用 Tab 分隔": 1,
    }
    assert [i.text for i in parsed.split("val")] == ["25/12/2018", "CASH"]


def test_ppocrlabel_folder_paths_are_found():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("receipts/a.png", png(size=(100, 80)))
        label = [{"transcription": "TOTAL", "points": [[1, 1], [50, 1], [50, 20], [1, 20]]}]
        archive.writestr("receipts/Label.txt", f"receipts/a.png\t{json.dumps(label)}\n")
    parsed = dataset_files.parse(out.getvalue(), "ocr_det")
    assert parsed.counts["all"] == 1
    assert parsed.items[0].boxes[0].text == "TOTAL"


def test_unsafe_zips_are_refused():
    with raises(HubError, "不安全的路径"):
        dataset_files.parse(rec_zip({"../evil.txt": b"x"}), "ocr_rec")
    with raises(HubError, "未找到标注文件"):
        dataset_files.parse(rec_zip(), "ocr_det")
    with raises(HubError, "不是 zip"):
        dataset_files.parse(b"hello", "ocr_rec")


def test_dataset_upload_download_delete(client, factory, storage):
    zipped = rec_zip()
    response = client.post(
        "/api/v1/hub/datasets",
        files={"file": ("rec.zip", zipped, "application/zip")},
        data={"name": "收据识别", "kind": "ocr_rec", "description": "外面标的"},
    )
    assert response.status_code == 201, response.text
    first = response.json()
    assert (first["version"], first["source"], first["counts"]["val"]) == (1, "upload", 2)
    again = client.post(
        "/api/v1/hub/datasets",
        files={"file": ("rec.zip", rec_zip(), "application/zip")},
        data={"name": "收据识别", "kind": "ocr_rec"},
    ).json()
    assert again["version"] == 2
    got = client.get(f"/api/v1/hub/datasets/{first['id']}/download")
    assert got.status_code == 200 and got.content == zipped
    assert client.delete(f"/api/v1/hub/datasets/{first['id']}").status_code == 204
    assert [d["version"] for d in client.get("/api/v1/hub/datasets").json()] == [2]
    with session_scope(factory) as session:
        assert session.query(Dataset).count() == 1


# ---- Evaluation ----


def test_text_metrics_follow_text_rec_metric():
    assert levenshtein("kitten", "sitting") == 3
    assert normalized_distance("9.00", "9.00") == 0.0
    assert normalized_distance("", "") == 0.0
    assert normalized_distance("100", "1.00") == 0.25  # One box missed: 1 / 4.


def test_detection_iou_follows_paddleocr():
    gt = (
        Box(((0, 0), (100, 0), (100, 20), (0, 20)), "TOTAL"),
        Box(((0, 50), (100, 50), (100, 70), (0, 70)), "###"),  # Illegible: not counted.
        Box(((0, 100), (100, 100), (100, 120), (0, 120)), "CASH"),
    )
    det = [
        [(2, 1), (100, 1), (100, 21), (2, 21)],  # Matches the first box.
        [(0, 50), (90, 50), (90, 70), (0, 70)],  # Inside a ### box: not counted.
        [(0, 100), (30, 100), (30, 120), (0, 120)],  # IoU 0.3: unmatched.
    ]
    result = evaluate_image(gt, det)
    assert (result.gt_care, result.det_care, result.matched) == (2, 2, 1)
    assert result.gt_hit == [True, False, False]


class FakeEngine:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers

    def read_line(self, image: Path):
        with Image.open(image) as picture:
            key = f"{picture.size[0]}x{picture.size[1]}"
        text = self.answers.get(key)
        return (text, 0.9) if text is not None else None

    def detect(self, image: Path):
        return []


def eval_zip() -> bytes:
    """Each image has a different size; the fake engine "recognizes" by size."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        rows = []
        for n, (w, text) in enumerate([(40, "TOTAL"), (41, "9.00"), (42, "CASH"), (43, "Date:")]):
            archive.writestr(f"img/{n}.png", png(size=(w, 12)))
            rows.append(f"img/{n}.png\t{text}\n")
        archive.writestr("rec_gt_val.txt", "".join(rows))
    return out.getvalue()


def test_eval_run_compares_two_models(client, factory, storage, queue, root, monkeypatch):
    dataset = client.post(
        "/api/v1/hub/datasets",
        files={"file": ("v.zip", eval_zip(), "application/zip")},
        data={"name": "验证", "kind": "ocr_rec"},
    ).json()
    response = client.post(
        "/api/v1/hub/evals",
        json={
            "dataset_id": dataset["id"],
            "split": "val",
            "a": {"source": "current"},
            "b": {"source": "builtin"},
        },
    )
    assert response.status_code == 400 and "同一模型" in response.json()["detail"]

    rec = root / "models" / "ocr" / "my_rec.onnx"
    rec.parent.mkdir(parents=True)
    rec.write_bytes(b"fake")
    (root / "models" / "selected.json").write_text(
        json.dumps({"ocr_rec": "models/ocr/my_rec.onnx"})
    )
    response = client.post(
        "/api/v1/hub/evals",
        json={
            "dataset_id": dataset["id"],
            "split": "val",
            "a": {"source": "current"},
            "b": {"source": "builtin"},
        },
    )
    assert response.status_code == 201, response.text
    run_id = uuid.UUID(response.json()["id"])
    assert queue.evals == [run_id]
    assert response.json()["arms"][0]["rec"] == "models/ocr/my_rec.onnx"

    a = FakeEngine({"40x12": "TOTAL", "41x12": "9.0", "42x12": "cash", "43x12": "Date:"})
    b = FakeEngine({"40x12": "TOTAL", "41x12": "9.00", "42x12": "CASH"})

    def engines(arm: EvalArm):
        return a if arm.name == "A" else b

    assert run_eval(run_id, factory, storage, engines) == "done"
    assert run_eval(run_id, factory, storage, engines) == "skipped"  # Not run twice.
    result = client.get(f"/api/v1/hub/evals/{run_id}").json()
    s = result["summary"]
    assert result["status"] == "done"
    assert (s["a"]["exact"], s["b"]["exact"]) == (2, 3)
    assert s["a"]["format_only"] == 1  # cash and CASH differ only in case.
    assert s["b"]["empty"] == 1
    assert (s["fixed"], s["broken"]) == (2, 1)
    assert s["evidence"] == "process" and s["grouped"] is False
    lines = client.get(f"/api/v1/hub/evals/{run_id}/lines")
    rows = [json.loads(x) for x in lines.text.splitlines()]
    assert rows[1] == {
        "image": "img/1.png",
        "group": "img:img/1.png",
        "truth": "9.00",
        "a": "9.0",
        "b": "9.00",
        "a_ok": False,
        "b_ok": True,
    }
    with session_scope(factory) as session:
        assert session.query(AuditLog).filter_by(action="hub:eval").count() == 1
    # Deleting a dataset deletes its evaluations too.
    assert client.delete(f"/api/v1/hub/datasets/{dataset['id']}").status_code == 204
    with session_scope(factory) as session:
        assert session.query(OcrEvalRun).count() == 0


def test_interrupted_eval_is_marked_failed(client, factory, storage, root):
    dataset = client.post(
        "/api/v1/hub/datasets",
        files={"file": ("v.zip", eval_zip(), "application/zip")},
        data={"name": "验证", "kind": "ocr_rec"},
    ).json()
    with session_scope(factory) as session:
        run = OcrEvalRun(
            dataset_id=uuid.UUID(dataset["id"]),
            split="val",
            task="rec",
            arms=[],
            status="running",
            user_id=uuid.UUID(int=1),
            team_id=uuid.UUID(int=1),
        )
        session.add(run)
        session.flush()
        run_id = run.id
    assert run_eval(run_id, factory, storage) == "interrupted"
    with session_scope(factory) as session:
        assert session.get_one(OcrEvalRun, run_id).status == "failed"


def test_command_line_eval_on_a_local_zip(tmp_path, root, monkeypatch, capsys):
    from jacara import cli
    from jacara.eval import ocr

    monkeypatch.setattr(ocr, "default_engine", lambda arm: FakeEngine({"40x12": "TOTAL"}))
    data = tmp_path / "val.zip"
    data.write_bytes(eval_zip())
    rec = root / "models" / "ocr" / "my_rec.onnx"
    rec.parent.mkdir(parents=True)
    rec.write_bytes(b"fake")
    (root / "models" / "selected.json").write_text(
        json.dumps({"ocr_rec": "models/ocr/my_rec.onnx"})
    )
    code = cli.main(
        [
            "eval",
            "ocr",
            "--dataset-file",
            str(data),
            "--b",
            "builtin",
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0, capsys.readouterr().out
    out = next((tmp_path / "out").iterdir())
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["a"]["lines"] == 4 and summary["arms"][1]["label"] == "RapidOCR 内置模型"
    assert "Exact Match" in (out / "summary.md").read_text("utf-8")
