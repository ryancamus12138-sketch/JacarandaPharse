"""Settings page: .env takes precedence, then values changed on the page (data/settings.json), then
defaults.

Background workers read the configuration once at startup, so changes require `jacara stop` and
`jacara start`; the API clears its cache immediately after a change."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from jacara.core import config
from jacara.core.config import PAGE_EDITABLE, Settings, get_settings
from jacara.core.i18n import N_, tr
from jacara.core.owner import Owner
from jacara.core.permissions import allowed, require
from jacara.modules.app_settings.schemas import Option, SettingItem, SettingsIn, SettingsOut
from jacara.modules.audit.service import record

STARTED = time.time()
"""Start time of this process: page settings newer than this have not been applied by a restart
yet."""


@dataclass(frozen=True)
class Spec:
    group: str
    label: str
    help: str
    kind: str
    options: list[tuple[str, str]] = field(default_factory=list)
    min: int | None = None
    max: int | None = None


SPECS: dict[str, Spec] = {
    "jacara_vision": Spec(
        N_("识别"),
        N_("由模型读取原图"),
        N_("照片、扫描件的 OCR 结果不准确时，由视觉模型读取原图。需先配置视觉模型。"),
        "choice",
        [
            ("auto", N_("仅在 OCR 不可靠时使用（推荐）")),
            ("always", N_("每张都使用（较慢）")),
            ("off", N_("不使用")),
        ],
    ),
    "jacara_pdf_read": Spec(
        N_("识别"),
        N_("PDF 文字读取方式"),
        N_(
            "渲染为图像后执行 OCR，与照片流程相同，PDF 文本层仅用于核对；"
            "仅读取文本层速度较快，但只适用于确认无误的电子版。"
        ),
        "choice",
        [("image", N_("渲染为图像后 OCR（推荐）")), ("text", N_("仅读取 PDF 文本层"))],
    ),
    "jacara_ocr_max_side": Spec(
        N_("识别"),
        N_("OCR 图像长边像素上限"),
        N_("超过上限的图像将先缩小。数值越大，小字识别越准确，但速度越慢。"),
        "int",
        min=1000,
        max=8000,
    ),
    "jacara_layout": Spec(
        N_("识别"),
        N_("版面分析"),
        N_("抽取前使用版面模型识别标题、正文、表格及页眉页脚。"),
        "bool",
    ),
    "jacara_learn": Spec(
        N_("从人工修改中学习"),
        N_("抽取时提供历史修改示例"),
        N_("将同一模板此前经人工修改的值提供给模型，以减少重复错误。"),
        "bool",
    ),
    "jacara_learn_mode": Spec(
        N_("从人工修改中学习"),
        N_("示例选择方式"),
        N_("整理后：仅保留模型导致的错误，合并重复项并去除过期项。切换前请先通过回放对比效果。"),
        "choice",
        [("recent", N_("近期修改较多的（默认）")), ("curated", N_("整理后"))],
    ),
    "jacara_pipeline": Spec(
        N_("后台和导入"),
        N_("后台双阶段并行"),
        N_("OCR 与模型并行运行，批量导入更快；关闭可节省数百 MB 内存。"),
        "bool",
    ),
    "jacara_watch_dir": Spec(
        N_("后台和导入"),
        N_("监视文件夹"),
        N_(
            "放入的图片和 PDF 将自动导入，原文件保持不变，重复文件将跳过"
            "。请填写完整路径；留空则不监视。"
        ),
        "path",
    ),
    "jacara_watch_template": Spec(
        N_("后台和导入"),
        N_("监视文件夹使用的模板"),
        N_("我的模板的 id；留空则使用上次上传时选择的模板。"),
        "text",
    ),
    "max_upload_mb": Spec(
        N_("后台和导入"),
        N_("上传文件大小上限（MB）"),
        N_("超过此大小的文件将被拒绝。"),
        "int",
        min=1,
        max=500,
    ),
    "jacara_api_docs": Spec(
        N_("安全"),
        N_("启用接口文档页 /docs"),
        N_("便于开发调试；供他人使用时建议关闭，以减少接口细节暴露。"),
        "bool",
    ),
}
if set(SPECS) != set(PAGE_EDITABLE):  # Both sides must change together.
    raise RuntimeError("SPECS and config.PAGE_EDITABLE list different settings")


class SettingsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def env_locked() -> set[str]:
    """Settings defined in environment variables or .env (not editable on the page). Only the
    presence of each name is checked; values are not read."""
    from dotenv import dotenv_values

    names = {k.upper() for k in os.environ}
    env_file = Settings.model_config.get("env_file")
    if isinstance(env_file, str | Path) and Path(env_file).is_file():
        names |= {k.upper() for k in dotenv_values(env_file)}
    return {key for key in PAGE_EDITABLE if key.upper() in names}


def _saved_at() -> float:
    try:
        data = json.loads(config.page_settings_path().read_text("utf-8"))
        return float(data.get("_saved_at", 0)) if isinstance(data, dict) else 0.0
    except (OSError, ValueError, TypeError):
        return 0.0


def _check(key: str, value: Any) -> Any:
    """Validate against the type of this setting in Settings and the settings page range. Returns a
    JSON-serializable value."""
    spec = SPECS[key]
    annotation: Any = Settings.model_fields[key].annotation
    try:
        checked = TypeAdapter(annotation).validate_python(value)
    except ValidationError as err:
        raise SettingsError("invalid", tr("{label}：值无效", label=tr(spec.label))) from err
    if spec.kind == "int" and (
        (spec.min is not None and checked < spec.min)
        or (spec.max is not None and checked > spec.max)
    ):
        raise SettingsError(
            "invalid",
            tr("{label}：需在 {min}～{max} 之间", label=tr(spec.label), min=spec.min, max=spec.max),
        )
    if spec.kind == "path" and checked is not None:
        path = Path(checked).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise SettingsError(
                "invalid", tr("{label}：需填写已存在文件夹的完整路径", label=tr(spec.label))
            )
        return str(path)
    if spec.kind == "text":
        return str(checked).strip() or None
    return checked


class AppSettingsService:
    def __init__(self, session: Session, owner: Owner) -> None:
        self.session = session
        self.owner = owner

    def get(self) -> SettingsOut:
        require(self.owner, "document:read")
        effective = Settings()
        locked = env_locked()
        saved = config.page_settings()
        items = []
        for key, spec in SPECS.items():
            value = getattr(effective, key)
            default = Settings.model_fields[key].default
            items.append(
                SettingItem(
                    key=key,
                    group=tr(spec.group),
                    label=tr(spec.label),
                    help=tr(spec.help),
                    kind=spec.kind,  # type: ignore[arg-type]
                    value=str(value) if isinstance(value, Path) else value,
                    default=str(default) if isinstance(default, Path) else default,
                    source="env" if key in locked else "page" if key in saved else "default",
                    env=key.upper(),
                    options=[Option(value=v, label=tr(label)) for v, label in spec.options],
                    min=spec.min,
                    max=spec.max,
                )
            )
        return SettingsOut(
            items=items,
            can_change="system:manage" in allowed(self.owner),
            restart_needed=_saved_at() > STARTED,
        )

    def update(self, data: SettingsIn) -> SettingsOut:
        require(self.owner, "system:manage")
        locked = env_locked()
        saved = config.page_settings()
        changed: dict[str, Any] = {}
        for key, value in data.values.items():
            if key not in SPECS:
                raise SettingsError("invalid", tr("设置页无法修改 {key}", key=key))
            if key in locked:
                raise SettingsError(
                    "invalid",
                    tr(
                        "{label}由 .env 中的 {env} 指定，无法在页面上修改",
                        label=tr(SPECS[key].label),
                        env=key.upper(),
                    ),
                )
            if value is None or value == "":
                saved.pop(key, None)
                changed[key] = None
            else:
                saved[key] = changed[key] = _check(key, value)
        if not changed:
            return self.get()
        path = config.page_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        body = {**saved, "_saved_at": time.time(), "_saved": datetime.now(UTC).isoformat()}
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), "utf-8")
        # Write to a temporary file and rename, so that a power loss never leaves a partially
        # written file.
        tmp.replace(path)
        get_settings.cache_clear()
        record(self.session, self.owner, "settings:update", None, None, **changed)
        return self.get()
