# PyInstaller spec of the desktop app's backend: one-folder mode (ADR 0068 section 7.1).
#
#   uv run pyinstaller packaging/desktop/jacara.spec --noconfirm
#
# Besides the code, the program reads files that PyInstaller does not find by following imports;
# each is listed here, and the build stops when one is missing:
# - jacara: starter templates (templates/*.toml), interface translations (locale/*/LC_MESSAGES/
#   messages.mo) and the database migrations (migrations/, read by Alembic as files);
# - RapidOCR: its detection, recognition and orientation models and its configuration;
# - rapid-layout: only the layout model layout_cdla.onnx. Its other weights (YOLOv8) are AGPL
#   licensed and must not ship (ADR 0068 section 3.5);
# - rapid-table: its configuration (the table model itself is ours, below);
# - the table structure model SLANet-plus, checked against its published SHA-256;
# - package metadata read at run time with importlib.metadata (versions shown in the app).
# Modules loaded by name (uvicorn's "jacara.app:app", uvicorn's protocol implementations, Alembic's
# environment script) are listed as hidden imports.
# ruff: noqa: F821  SPECPATH, Analysis, PYZ, EXE and COLLECT are provided by PyInstaller.

import hashlib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
JACARA = ROOT / "backend" / "src" / "jacara"

TABLE_MODEL = ROOT / "models" / "table" / "slanet-plus.onnx"
TABLE_MODEL_SHA256 = "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"
"""Published in README.md (installation step for the table model)."""


def fail(message: str) -> None:
    raise SystemExit(f"jacara.spec: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---- Program files that are read as files ----
datas = []
templates = sorted(JACARA.glob("templates/**/*.toml"))
catalogs = sorted(JACARA.glob("locale/*/LC_MESSAGES/messages.mo"))
migrations = sorted(
    p for p in (JACARA / "migrations").rglob("*") if p.is_file() and "__pycache__" not in p.parts
)
if not templates or not catalogs or not any(p.name == "env.py" for p in migrations):
    fail("templates, translations or migrations not found under backend/src/jacara")
for path in [*templates, *catalogs, *migrations]:
    datas.append((str(path), str(Path("jacara") / path.parent.relative_to(JACARA))))

# ---- Models ----
rapidocr = collect_data_files("rapidocr")
if not any(dest.endswith("models") and src.endswith(".onnx") for src, dest in rapidocr):
    fail("RapidOCR models not found in the rapidocr package")
datas += rapidocr

layout = collect_data_files("rapid_layout")
weights = [src for src, _dest in layout if src.endswith((".onnx", ".pt", ".pth", ".engine"))]
if [Path(w).name for w in weights] != ["layout_cdla.onnx"]:
    fail(f"rapid-layout must ship only layout_cdla.onnx (other weights are AGPL), found {weights}")
datas += layout
datas += [(src, dest) for src, dest in collect_data_files("rapid_table") if not src.endswith(".onnx")]

if not TABLE_MODEL.is_file():
    fail(f"table model missing: {TABLE_MODEL} (see README.md, table model)")
if sha256(TABLE_MODEL) != TABLE_MODEL_SHA256:
    fail(f"table model checksum differs from the published one: {TABLE_MODEL}")
datas.append((str(TABLE_MODEL), "models/table"))

# ---- Other package files and metadata ----
datas += collect_data_files("fastapi_offline")
for name in ("jacara", "rapidocr", "rapid_layout", "rapid_table", "pdfplumber", "onnxruntime"):
    datas += copy_metadata(name)

hiddenimports = [
    *collect_submodules("jacara", filter=lambda name: ".migrations" not in name),
    *collect_submodules("uvicorn"),
    *collect_submodules("alembic"),
    *collect_submodules("huey"),
]

a = Analysis(
    [str(Path(SPECPATH) / "jacara_desktop.py")],
    pathex=[str(ROOT / "backend" / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    # The desktop app uses SQLite, local files and Huey: no Celery, Valkey, S3 or PostgreSQL client.
    # Leaving out psycopg also keeps its LGPL licence out of the installer (ADR 0068 section 7.1).
    excludes=[
        "celery",
        "kombu",
        "redis",
        "boto3",
        "botocore",
        "psycopg",
        "psycopg_binary",
        "psycopg_c",
        "tkinter",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jacara-backend",
    console=True,
)
COLLECT(exe, a.binaries, a.datas, name="jacara-backend")
