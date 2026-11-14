"""换模型（ADR 0046）。

只能选 models/ 文件夹里已有的文件（离线：不下载）。选的结果写进 models/selected.json，
.env 里写了的（JACARA_MODEL 等）优先，网页上显示"由 .env 指定"。
大模型和视觉模型在 jacara start 时加载，OCR、表格模型在后台任务启动时加载，
所以改完要 jacara stop、jacara start 才生效；页面上比较"选的"和"正在用的"，不一样就提醒。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from jacara.core.config import CHOICES_FILE, PROJECT_ROOT, Settings, is_mmproj, model_choices
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.modules.audit.service import record
from jacara.modules.system.service import model_props

MODELS = PROJECT_ROOT / "models"

SLOTS: dict[str, str] = {
    "model": "大模型",
    "mmproj": "视觉模型",
    "ocr_det": "OCR 文字检测",
    "ocr_rec": "OCR 文字识别",
    "ocr_dict": "OCR 识别字典",
    "table": "表格结构模型",
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
    """相对项目根目录，例如 models/Qwen3.5-9B-Q4_K_M.gguf。"""
    name: str
    size: int
    modified: datetime


class Slot(BaseModel):
    key: str
    label: str
    current: str | None
    """现在生效的文件（相对路径）；None：用自带的（OCR）或没有。"""
    source: str
    """env：.env 指定（网页上改不了）；choice：网页上选的；auto：models/ 里自动找到的；
    builtin：用 RapidOCR 自带的；none：没有。"""
    env: str
    files: list[ModelFile]


class ModelsOut(BaseModel):
    slots: list[Slot]
    loaded: str | None
    """模型服务现在加载的大模型文件名（连不上是 None）。"""
    restart_needed: bool
    """选的大模型和正在用的不一样：要 jacara stop、jacara start。"""


class ModelsIn(BaseModel):
    """要换的：键是 SLOTS 里的，值是 models/ 里的文件（相对路径）；空字符串 = 不用（视觉模型）
    或用自带的（OCR、表格）；不写的不变。"""

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
    for path in sorted(MODELS.glob(pattern)):
        if not path.is_file() or (callable(keep) and not keep(path)):
            continue
        stat = path.stat()
        found.append(
            ModelFile(
                path=str(path.relative_to(PROJECT_ROOT)),
                name=path.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return found


def candidates(slot: str) -> list[ModelFile]:
    """每一项能选哪些文件：models/ 里按 ADR 0046 的位置放的，
    加上模型库（ADR 0059）里这一种的版本。"""
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
    else:
        raise KeyError(slot)
    return found + _hub_files(slot)


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
                path=str(path.relative_to(PROJECT_ROOT)),
                name=f"{v.name} v{v.version}（模型库）",
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return found


def _relative(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
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
        return _relative(path), "auto"

    def get(self) -> ModelsOut:
        require(self.owner, "document:read")
        slots = []
        for key, label in SLOTS.items():
            current, source = self._current(key)
            slots.append(
                Slot(
                    key=key,
                    label=label,
                    current=current,
                    source=source,
                    env=ENV_NAMES[key],
                    files=candidates(key),
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
                    raise ModelError(f"{SLOTS[key]}只能选 models/ 里已有的文件，没有 {value}")
            elif key == "model":
                raise ModelError("大模型不能不用")
            choices[key] = value
            changed[key] = value
        target = PROJECT_ROOT / CHOICES_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(choices, ensure_ascii=False, indent=2), "utf-8")
        record(self.session, self.owner, "system:models", None, None, **changed)
        return self.get()
