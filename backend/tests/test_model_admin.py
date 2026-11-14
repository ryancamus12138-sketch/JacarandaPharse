"""Model selection: only files in models/ can be selected, .env takes precedence, and a restart
reminder follows changes."""

from __future__ import annotations

import json

import pytest

from jacara.core import config
from jacara.modules.model_admin import service


@pytest.fixture
def models(tmp_path, monkeypatch):
    root = tmp_path
    (root / "models" / "ocr").mkdir(parents=True)
    (root / "models" / "table").mkdir()
    for name in ("A-Q4.gguf", "B-Q8.gguf", "mmproj-F16.gguf"):
        (root / "models" / name).write_bytes(b"x")
    (root / "models" / "ocr" / "my_det.onnx").write_bytes(b"x")
    (root / "models" / "ocr" / "my_rec.onnx").write_bytes(b"x")
    (root / "models" / "ocr" / "keys.txt").write_text("a")
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(service, "model_props", lambda _s: {})
    return root


def slots(client) -> dict[str, dict]:
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    return {s["key"]: s for s in response.json()["slots"]}


def test_lists_files_and_choices(client, models):
    found = slots(client)
    assert [f["name"] for f in found["model"]["files"]] == ["A-Q4.gguf", "B-Q8.gguf"]
    assert found["model"]["source"] == "none"  # Two LLMs: no guessing.
    assert found["mmproj"]["source"] == "auto"
    assert found["ocr_det"]["source"] == "builtin"
    response = client.put(
        "/api/v1/models",
        json={"model": "models/B-Q8.gguf", "ocr_det": "models/ocr/my_det.onnx", "mmproj": ""},
    )
    assert response.status_code == 200, response.text
    found = {s["key"]: s for s in response.json()["slots"]}
    assert found["model"]["current"] == "models/B-Q8.gguf" and found["model"]["source"] == "choice"
    assert found["mmproj"]["current"] is None  # Vision projector disabled.
    assert found["ocr_det"]["current"] == "models/ocr/my_det.onnx"
    saved = json.loads((models / "models" / "selected.json").read_text())
    assert saved["model"] == "models/B-Q8.gguf"
    assert config.get_settings().ocr_models()["det"].name == "my_det.onnx"


def test_only_files_in_models_folder(client, models):
    response = client.put("/api/v1/models", json={"model": "../../etc/passwd"})
    assert response.status_code == 400
    response = client.put("/api/v1/models", json={"model": ""})
    assert response.status_code == 400


def test_env_wins(client, models, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "jacara_model", models / "models" / "A-Q4.gguf")
    client.put("/api/v1/models", json={"model": "models/B-Q8.gguf"})
    found = slots(client)
    assert found["model"]["source"] == "env" and found["model"]["current"] == "models/A-Q4.gguf"
