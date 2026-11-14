"""Download the table structure model SLANet-plus that the desktop app ships, and check it.

    uv run --group desktop python packaging/desktop/fetch_table_model.py

Writes models/table/slanet-plus.onnx, where jacara.spec takes it from (a copy that is already there
and matches is kept). The address, size and SHA-256 are the ones the installation steps in
README.md publish; a difference stops the build. The model is Apache-2.0 (RapidAI/RapidTable, from
PaddleOCR)."""

from __future__ import annotations

from pathlib import Path

from fetch_llama import ROOT, download

URL = "https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx"
SIZE = 7758305
SHA256 = "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"
TARGET = ROOT / "models" / "table" / "slanet-plus.onnx"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    download(URL, TARGET, SIZE, SHA256)
    print(f"table model: {Path(TARGET).relative_to(ROOT)} ({SIZE / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
