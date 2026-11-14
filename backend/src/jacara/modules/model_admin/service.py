"""Model selection.

Only files already present in models/ can be selected (offline; nothing is downloaded). The
selection is written to models/selected.json; values set in .env (JACARA_MODEL and others) take
precedence and the web UI shows "set by .env". The LLM and vision projector are loaded by `jacara
start`, OCR and table models when background workers start, so changes take effect after `jacara
stop` and `jacara start`; the page compares the selection with what is running and warns when
they differ."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from jacara.core import config
from jacara.core.config import (
    CHOICES_FILE,
    Settings,
    bundled_table_model,
    is_mmproj,
    model_choices,
)
from jacara.core.i18n import N_, tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.modules.audit.service import record
from jacara.modules.system.service import model_props


def _models() -> Path:
    """models/ in the data directory. Resolved on every call because tests replace the data
    directory with a temporary one."""
    return config.data_home() / "models"


SLOTS: dict[str, str] = {
    "model": N_("大模型"),
    "mmproj": N_("视觉模型"),
    "ocr_det": N_("OCR 文字检测"),
    "ocr_rec": N_("OCR 文字识别"),
    "ocr_dict": N_("OCR 识别字典"),
    "table": N_("表格结构模型"),
}

ENV_NAMES = {
    "model": "JACARA_MODEL",
    "mmproj": "JACARA_MMPROJ",
    "ocr_det": "JACARA_OCR_DET",
    "ocr_rec": "JACARA_OCR_REC",
    "ocr_dict": "JACARA_OCR_DICT",
    "table": "JACARA_TABLE_MODEL",
}


class ModelFile(BaseModel):
    path: str
    """Relative to the project root, for example models/Qwen3.5-9B-Q4_K_M.gguf."""
    name: str
    size: int
    modified: datetime
    provider: str | None = None
    """Who ships the file when it is not imported (the table model shipped with the program)."""


class Slot(BaseModel):
    key: str
    label: str
    current: str | None
    """File in effect (relative path); None: bundled (OCR) or none."""
    source: str
    """env: set in .env (not editable in the web UI); choice: selected in the web UI; auto: detected
    in models/; builtin: bundled with RapidOCR; bundled: shipped with the program (the table
    model); none: none."""
    env: str
    files: list[ModelFile]
    default_label: str | None = None
    """What the slot uses when nothing is chosen, with where it comes from (OCR slots: the models
    shipped inside RapidOCR); None when the slot has no such default."""


class ModelsOut(BaseModel):
    slots: list[Slot]
    loaded: str | None
    """LLM file currently loaded by the model server (None if unreachable)."""
    restart_needed: bool
    """The selected LLM differs from the running one: `jacara stop` and `jacara start` required."""


class ModelsIn(BaseModel):
    """Changes: keys from SLOTS, values are files in models/ (relative paths); an empty string
    disables the slot (vision projector) or uses the bundled model (OCR, table); omitted keys are
    unchanged."""

    model: str | None = None
    mmproj: str | None = None
    ocr_det: str | None = None
    ocr_rec: str | None = None
    ocr_dict: str | None = None
    table: str | None = None


class ModelError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _files(pattern: str, keep: object = None) -> list[ModelFile]:
    found = []
    for path in sorted(_models().glob(pattern)):
        if not path.is_file() or (callable(keep) and not keep(path)):
            continue
        stat = path.stat()
        found.append(
            ModelFile(
                path=path.relative_to(config.data_home()).as_posix(),
                name=path.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return found


def candidates(slot: str) -> list[ModelFile]:
    """Selectable files per slot: the default shipped with the program (table model) and the
    imported versions of this kind; outside the desktop app also files placed in the expected
    locations under models/. The desktop app runs imported models only (ADR 0068): loose files are
    offered for import in the setup wizard instead."""
    if config.get_settings().jacara_desktop:
        found: list[ModelFile] = []
        if slot == "table":
            found += _bundled()
        return found + _hub_files(slot)
    if slot == "model":
        found = _files("*.gguf", lambda p: not is_mmproj(p))
    elif slot == "mmproj":
        found = _files("*.gguf", is_mmproj)
    elif slot == "ocr_det":
        found = _files("ocr/**/*.onnx", lambda p: "det" in p.name.lower())
    elif slot == "ocr_rec":
        found = _files("ocr/**/*.onnx", lambda p: "rec" in p.name.lower())
    elif slot == "ocr_dict":
        found = _files("ocr/**/*.txt")
    elif slot == "table":
        found = _files("table/**/*.onnx")
        found += [f for f in _bundled() if f.path not in {f.path for f in found}]
    else:
        raise KeyError(slot)
    return found + _hub_files(slot)


def _bundled() -> list[ModelFile]:
    """The table structure model shipped with the program: the default."""
    bundled = bundled_table_model()
    if bundled is None:
        return []
    stat = bundled.stat()
    return [
        ModelFile(
            path=_relative(bundled) or str(bundled),
            name="SLANet-plus",
            # A PaddleOCR model (PP-Structure) that comes with the program: "built-in" as VS Code
            # calls what comes with it, with the author named so that it is not taken for ours.
            provider=tr("PaddleOCR 模型，程序内置"),
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime, UTC),
        )
    ]


RAPIDOCR_DEFAULTS = {"ocr_det": "PP-OCRv6_det_small.onnx", "ocr_rec": "PP-OCRv6_rec_small.onnx"}
"""The models RapidOCR uses when none is chosen (shipped in its models folder; plugins/ocr/rapid.py
records them as the defaults)."""


def default_label(slot: str) -> str | None:
    """Label of the "nothing chosen" choice of an OCR slot: the PaddleOCR model RapidOCR ships and
    uses (who made it, and who distributes it), and
    for the dictionary the character table stored in the recognition model (RapidOCR reads it from
    the model's "character" metadata when no dictionary file is given)."""
    if slot in RAPIDOCR_DEFAULTS:
        from importlib.metadata import PackageNotFoundError, version

        try:
            engine = version("rapidocr")
        except PackageNotFoundError:
            engine = ""
        return tr(
            "{model}（PaddleOCR 模型，RapidOCR {version} 内置）",
            model=Path(RAPIDOCR_DEFAULTS[slot]).stem,
            version=engine,
        )
    if slot == "ocr_dict":
        return tr("识别模型内嵌字符表")
    return None


def default_file(slot: str) -> Path | None:
    """The file a slot uses when nothing is chosen, when it is a file: the table model shipped with
    the program, or RapidOCR's own detection and recognition models."""
    if slot == "table":
        return bundled_table_model()
    if slot in RAPIDOCR_DEFAULTS:
        spec = find_spec("rapidocr")
        if spec is None or spec.origin is None:
            return None
        path = Path(spec.origin).parent / "models" / RAPIDOCR_DEFAULTS[slot]
        return path if path.is_file() else None
    return None


@lru_cache(maxsize=16)
def _sha256(path: str, size: int, modified: float) -> str:
    """SHA-256 of a file, kept while its size and modification time stay the same."""
    del size, modified  # Part of the cache key only.
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def same_as_default(slot: str) -> set[str]:
    """Imported versions (paths) that are the very file the slot uses by default: shown once, as
    the default."""
    from jacara.modules.hub import registry

    default = default_file(slot)
    if default is None:
        return set()
    stat = default.stat()
    sha = _sha256(str(default), stat.st_size, stat.st_mtime)
    return {
        _relative(v.weights) or ""
        for v in registry.versions()
        if v.kind == slot and any(f.role == "weights" and f.sha256 == sha for f in v.files)
    }


def _hub_files(slot: str) -> list[ModelFile]:
    from jacara.modules.hub import registry

    found = []
    for v in registry.versions():
        path = v.dictionary if slot == "ocr_dict" and v.kind == "ocr_rec" else None
        if v.kind == slot:
            path = v.weights
        if path is None:
            continue
        stat = path.stat()
        found.append(
            ModelFile(
                path=path.relative_to(config.data_home()).as_posix(),
                name=tr("{name} v{version}", name=v.name, version=v.version),
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return found


def _relative(path: Path | None) -> str | None:
    """The path as the file lists give it (relative to the data directory), so the current file
    matches its entry in the list. Compared as written first, then with links resolved: a model
    file in models/ may be a link to a file elsewhere, and resolving it alone would leave the data
    directory and show the full path instead of the file."""
    if path is None:
        return None
    home = config.data_home()
    for candidate, base in ((path.absolute(), home.absolute()), (path.resolve(), home.resolve())):
        try:
            return candidate.relative_to(base).as_posix()
        except ValueError:
            continue
    return str(path)


class ModelAdminService:
    def __init__(self, session: Session, owner: Owner, settings: Settings) -> None:
        self.session = session
        self.owner = owner
        self.settings = settings

    def _current(self, slot: str) -> tuple[str | None, str]:
        s = self.settings
        choices = model_choices()
        env = {
            "model": s.jacara_model,
            "mmproj": s.jacara_mmproj,
            "ocr_det": s.jacara_ocr_det,
            "ocr_rec": s.jacara_ocr_rec,
            "ocr_dict": s.jacara_ocr_dict,
            "table": s.jacara_table_model,
        }[slot]
        if slot == "model":
            path = s.find_model()
        elif slot == "mmproj":
            path = s.find_mmproj()
        elif slot == "table":
            path = s.table_model()
        else:
            path = s.ocr_models().get(slot.removeprefix("ocr_"))
        if env is not None:
            return _relative(path), "env"
        if choices.get(slot):
            return _relative(path), "choice" if path is not None else "none"
        if path is None:
            return None, "builtin" if slot.startswith("ocr_") else "none"
        if slot == "table" and path == bundled_table_model():
            return _relative(path), "bundled"
        return _relative(path), "auto"

    def get(self) -> ModelsOut:
        require(self.owner, "document:read")
        slots = []
        for key, label in SLOTS.items():
            current, source = self._current(key)
            # An imported copy of the default is not listed a second time (unless it is chosen).
            hidden = same_as_default(key) - {current or ""}
            files = [f for f in candidates(key) if f.path not in hidden]
            if current and source != "env" and current not in {f.path for f in files}:
                # A file chosen earlier that is no longer offered (a loose file in models/).
                files += [f for f in _files(current.removeprefix("models/")) if f.path == current]
            slots.append(
                Slot(
                    key=key,
                    label=tr(label),
                    current=current,
                    source=source,
                    env=ENV_NAMES[key],
                    files=files,
                    default_label=default_label(key),
                )
            )
        path = model_props(self.settings.model_server).get("model_path")
        loaded = str(path).replace("\\", "/").rsplit("/", 1)[-1] if path else None
        chosen = slots[0].current
        restart = bool(loaded and chosen and Path(chosen).name != loaded)
        return ModelsOut(slots=slots, loaded=loaded, restart_needed=restart)

    def choose(self, data: ModelsIn) -> ModelsOut:
        require(self.owner, "system:manage")
        choices = model_choices()
        changed: dict[str, str] = {}
        for key, value in data.model_dump(exclude_none=True).items():
            if value:
                allowed = {f.path for f in candidates(key)}
                if value not in allowed:
                    raise ModelError(
                        tr(
                            "{slot}只能选择 models/ 中已有的文件，未找到 {file}",
                            slot=tr(SLOTS[key]),
                            file=value,
                        )
                    )
            elif key == "model":
                raise ModelError(tr("必须选择大模型"))
            choices[key] = value
            changed[key] = value
        target = config.data_home() / CHOICES_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(choices, ensure_ascii=False, indent=2), "utf-8")
        record(self.session, self.owner, "system:models", None, None, **changed)
        return self.get()
