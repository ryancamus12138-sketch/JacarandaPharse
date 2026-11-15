"""Shipped llama.cpp builds: which one starts, GPU detection from `llama-server --list-devices`
(checked once per build), and the setting that is shown only when there is a choice to make."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from jacara.core import config
from jacara.core.config import Settings, get_settings
from jacara.plugins.model_backends import llama_builds
from jacara.plugins.model_backends.server import llama_server_path

WITH_GPU = """\
ggml_vulkan: Found 1 Vulkan devices:
Available devices:
  Vulkan0: AMD Radeon RX 7600 (8176 MiB, 7980 MiB free)
"""
WITHOUT_GPU = "Available devices:\n  (none)\n"


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A package folder with the given builds, and an empty data folder."""
    resources = tmp_path / "resources"
    monkeypatch.setattr(config, "resource_dir", lambda: resources)
    monkeypatch.setenv("JACARA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JACARA_LLAMA_SERVER", raising=False)
    monkeypatch.delenv("JACARA_LLAMA_DEVICE", raising=False)
    get_settings.cache_clear()

    def make(*variants: str) -> dict[str, Path]:
        made = {}
        for variant in variants:
            exe = resources / "llama" / variant / llama_builds.EXE
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"llama-server " + variant.encode())
            made[variant] = exe
        return made

    return make


@pytest.fixture
def list_devices(monkeypatch):
    """Replace `llama-server --list-devices`; returns the list of commands run."""
    runs: list[list[str]] = []
    answer = {"stdout": WITH_GPU}

    def run(command, **_kwargs):
        runs.append(command)
        return subprocess.CompletedProcess(command, 0, answer["stdout"], "")

    monkeypatch.setattr(llama_builds.subprocess, "run", run)
    return runs, answer


def test_device_lines_are_read_from_list_devices(list_devices, tmp_path):
    _runs, answer = list_devices
    exe = tmp_path / "llama-server"
    assert llama_builds.gpu_devices(exe) == [
        "Vulkan0: AMD Radeon RX 7600 (8176 MiB, 7980 MiB free)"
    ]
    answer["stdout"] = WITHOUT_GPU
    assert llama_builds.gpu_devices(exe) == []


def test_a_broken_driver_counts_as_no_gpu(monkeypatch, tmp_path):
    def hangs(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, llama_builds.LIST_TIMEOUT_S)

    monkeypatch.setattr(llama_builds.subprocess, "run", hangs)
    assert llama_builds.gpu_devices(tmp_path / "llama-server") == []


def test_running_from_source_uses_no_shipped_build(shipped):
    assert llama_builds.bundled() == {}
    assert llama_builds.choose(Settings()) is None
    assert llama_builds.device_note() is None


def test_one_build_is_used_without_checking_devices(shipped, list_devices):
    runs, _answer = list_devices
    builds = shipped("metal")
    assert llama_builds.choose(Settings()) == builds["metal"]
    assert llama_builds.device_note() is None  # Nothing to choose: the setting is not shown.
    assert runs == []


def test_automatic_choice_follows_the_vulkan_build_devices(shipped, list_devices):
    runs, answer = list_devices
    builds = shipped("cpu", "vulkan")
    assert llama_builds.choose(Settings()) == builds["vulkan"]
    assert runs == [[str(builds["vulkan"]), "--list-devices"]]

    # Checked once per build: the answer is kept in the data folder.
    assert llama_builds.choose(Settings()) == builds["vulkan"]
    assert len(runs) == 1
    saved = json.loads((config.data_home() / llama_builds.DEVICES_FILE).read_text("utf-8"))
    assert saved["devices"] == ["Vulkan0: AMD Radeon RX 7600 (8176 MiB, 7980 MiB free)"]

    # A new build (another size or modification time) is checked again.
    answer["stdout"] = WITHOUT_GPU
    builds["vulkan"].write_bytes(b"llama-server vulkan, next release")
    os.utime(builds["vulkan"], (1, 1))
    assert llama_builds.choose(Settings()) == builds["cpu"]
    assert len(runs) == 2


def test_the_setting_overrides_the_automatic_choice(shipped, list_devices, monkeypatch):
    runs, _answer = list_devices
    builds = shipped("cpu", "vulkan")
    monkeypatch.setenv("JACARA_LLAMA_DEVICE", "cpu")
    assert llama_builds.choose(Settings()) == builds["cpu"]
    monkeypatch.setenv("JACARA_LLAMA_DEVICE", "vulkan")
    assert llama_builds.choose(Settings()) == builds["vulkan"]
    assert runs == []  # A fixed choice needs no detection.


def test_jacara_llama_server_comes_before_the_shipped_build(shipped, tmp_path, monkeypatch):
    builds = shipped("metal")
    assert llama_server_path(Settings()) == builds["metal"]
    own = tmp_path / "own" / "llama-server"
    own.parent.mkdir()
    own.write_bytes(b"")
    monkeypatch.setenv("JACARA_LLAMA_SERVER", str(own))
    assert llama_server_path(Settings()) == own


def test_device_note_names_what_was_found(shipped, list_devices):
    _runs, answer = list_devices
    shipped("cpu", "vulkan")
    assert "AMD Radeon RX 7600" in str(llama_builds.device_note())
    (config.data_home() / llama_builds.DEVICES_FILE).unlink()
    answer["stdout"] = WITHOUT_GPU
    note = str(llama_builds.device_note())
    assert "Vulkan" in note and "CPU" in note


def settings_items(client) -> dict[str, dict]:
    response = client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    return {i["key"]: i for i in response.json()["items"]}


def test_device_setting_is_shown_only_with_a_choice(client, shipped, list_devices, monkeypatch):
    monkeypatch.setenv("JACARA_DESKTOP", "true")
    get_settings.cache_clear()
    shipped("metal")
    assert "jacara_llama_device" not in settings_items(client)

    shipped("cpu", "vulkan")
    item = settings_items(client)["jacara_llama_device"]
    assert item["section"] == "models"
    assert item["value"] == "auto"
    assert {o["value"] for o in item["options"]} == {"auto", "cpu", "vulkan"}
    assert "AMD Radeon RX 7600" in item["help"]
