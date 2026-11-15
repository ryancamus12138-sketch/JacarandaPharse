# PyInstaller spec of the desktop app's backend: one-folder mode.
#
#   uv run pyinstaller packaging/desktop/jacara.spec --noconfirm
#
# Besides the code, the program reads files that PyInstaller does not find by following imports;
# each is listed here, and the build stops when one is missing:
# - jacara: starter templates (templates/*.toml), interface translations (locale/*/LC_MESSAGES/
#   messages.mo), the database migrations (migrations/, read by Alembic as files) and the list of
#   models offered for download (downloads/models.lock, checked by parsing it);
# - RapidOCR: its detection, recognition and orientation models and its configuration;
# - rapid-layout: only the layout model layout_cdla.onnx. Its other weights (YOLOv8) are AGPL
#   licensed and must not ship;
# - rapid-table: its configuration (the table model itself is ours, below);
# - the table structure model SLANet-plus, checked against its published SHA-256;
# - the llama.cpp builds prepared by fetch_llama.py (llama/<variant>/);
# - LICENSE, NOTICE and the third-party licence list made by licenses.py (shown under Settings →
#   About and diagnostics; Apache 2.0 section 4(d) asks for the NOTICE to ship);
# - package metadata read at run time with importlib.metadata (versions shown in the app).
# Modules loaded by name (uvicorn's "jacara.app:app", uvicorn's protocol implementations, Alembic's
# environment script) are listed as hidden imports.
# ruff: noqa: F821  SPECPATH, Analysis, PYZ, EXE and COLLECT are provided by PyInstaller.

import hashlib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parents[1]
JACARA = ROOT / "backend" / "src" / "jacara"
BUILD = ROOT / "build" / "desktop"

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
model_list = JACARA / "downloads" / "models.lock"
try:
    from jacara.downloads import parse_lock

    parse_lock(model_list.read_text("utf-8"))
except Exception as err:  # A malformed list must not ship.
    fail(f"downloads/models.lock: {err}")
for path in [*templates, *catalogs, *migrations, model_list]:
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

# ---- llama.cpp ----
llama = BUILD / "llama"
servers = sorted(llama.glob("*/llama-server*"))
if not servers:
    fail(f"no llama.cpp build in {llama}: run packaging/desktop/fetch_llama.py first")
for variant in sorted(p for p in llama.iterdir() if p.is_dir()):
    datas += [(str(f), f"llama/{variant.name}") for f in sorted(variant.iterdir()) if f.is_file()]

# ---- Licences ----
licences = [ROOT / "LICENSE", ROOT / "NOTICE", BUILD / "THIRD_PARTY_LICENSES"]
for path in licences:
    if not path.is_file():
        fail(f"{path} missing (THIRD_PARTY_LICENSES: run packaging/desktop/licenses.py first)")
    datas.append((str(path), "."))

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
    # Leaving out psycopg also keeps its LGPL licence out of the installer.
    excludes=[
        "celery",
        "kombu",
        "redis",
        "boto3",
        "botocore",
        "psycopg",
        "psycopg_binary",
        "psycopg_c",
        # Used only by the evaluation commands (development tools); its macOS wheel also carries
        # the GCC Fortran runtime, which the app does not need.
        "scipy",
        "tkinter",
    ],
)
pyz = PYZ(a.pure)
# Python's UTF-8 mode (PEP 540) in the packaged program. Without it Python on Windows uses the
# system's code page (for example cp1252 or cp936) for standard output and error when they are
# pipes, and for subprocess text: the desktop shell reads the backend's output as UTF-8, and the
# tests run in UTF-8 mode on every system (PYTHONUTF8=1 in CI; macOS and Linux default to UTF-8).
# A packaged program does not read PYTHON* environment variables, so the mode is set here, as the
# PyInstaller manual's "Specifying Python Interpreter Options" shows. No effect on macOS, where
# the locale is UTF-8 already.
options = [("X utf8_mode=1", None, "OPTION")]

exe = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="jacara-backend",
    console=True,
)
COLLECT(exe, a.binaries, a.datas, name="jacara-backend")
