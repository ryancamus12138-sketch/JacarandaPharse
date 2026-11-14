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
from jacara.core.config import PAGE_EDITABLE, Settings, WatchFolder, get_settings
from jacara.core.i18n import N_, tr
from jacara.core.owner import Owner
from jacara.core.permissions import allowed, require
from jacara.desktop import RUNTIME
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
    section: str = "processing"
    """Settings page that shows it: processing (Recognition and processing), data (Data and
    backups) or models (Models)."""
    desktop_only: bool = False
    """Only meaningful in the desktop app (its launcher does the work)."""
    live: bool = False
    """Read again on every use by the process that serves the page (the desktop launcher's
    automatic backup), so a change applies without restarting and does not ask for a restart."""
    needs_builds: bool = False
    """Only when the app ships several llama.cpp builds to choose from (Windows)."""


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
        N_(
            "决定把哪些历史修改给模型看。近期修改较多的：最常被改、最近改过的字段优先，"
            "每个字段最多 2 条，共最多 8 条。整理后：只用模型自己造成的错误（OCR 认错的字不算），"
            "同类错误合并计数；模板字段改过后，旧示例不再使用；同一开票方已有 3 份以上"
            "审核过的单据时，优先用它自己的示例。"
        ),
        "choice",
        [
            ("recent", N_("近期修改较多的（默认）")),
            ("curated", N_("整理后（只用模型的错误）")),
        ],
    ),
    "jacara_pipeline": Spec(
        N_("后台和导入"),
        N_("后台双阶段并行"),
        N_("OCR 与模型并行运行，批量导入更快；关闭可节省数百 MB 内存。"),
        "bool",
    ),
    "jacara_watch_folders": Spec(
        N_("后台和导入"),
        N_("监视文件夹"),
        N_(
            "放入这些文件夹（含子文件夹）的图片和 PDF 将自动导入，原文件保持不变，重复文件将跳过。"
            "每个文件夹可以使用不同的模板。"
        ),
        "watch_folders",
    ),
    "max_upload_mb": Spec(
        N_("后台和导入"),
        N_("上传文件大小上限（MB）"),
        N_("超过此大小的文件将被拒绝。"),
        "int",
        min=1,
        max=500,
    ),
    "jacara_auto_backup": Spec(
        N_("自动备份"),
        N_("每天自动备份数据库"),
        N_(
            "每天把数据库备份到数据文件夹的 backups/auto/。只含数据库，不含上传的文件；"
            "需要完整保护时，请定期用「立即备份」存到另一块磁盘。"
        ),
        "bool",
        section="data",
        desktop_only=True,
        live=True,
    ),
    "jacara_auto_backup_keep": Spec(
        N_("自动备份"),
        N_("保留的自动备份份数"),
        N_("超过这个数目时，删除最早的自动备份。"),
        "int",
        min=1,
        max=365,
        section="data",
        desktop_only=True,
        live=True,
    ),
    "jacara_api_docs": Spec(
        N_("安全"),
        N_("启用接口文档页 /docs"),
        N_("便于开发调试；供他人使用时建议关闭，以减少接口细节暴露。"),
        "bool",
    ),
    "jacara_llama_device": Spec(
        N_("计算设备"),
        N_("运行大模型的设备"),
        N_("自动：有可用的显卡（Vulkan）就用显卡，否则用 CPU。显卡运行出错时可改为 CPU。"),
        "choice",
        [
            ("auto", N_("自动（推荐）")),
            ("cpu", N_("CPU")),
            ("vulkan", N_("显卡（Vulkan）")),
        ],
        section="models",
        desktop_only=True,
        needs_builds=True,
    ),
}
LEGACY = frozenset({"jacara_watch_dir", "jacara_watch_template"})
"""Read from the page file as saved before there could be several watch folders; shown and saved as
jacara_watch_folders (they are dropped from the file when the list is saved)."""
if set(SPECS) | LEGACY != set(PAGE_EDITABLE):  # Both sides must change together.
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
    if config.reads_env_file() and isinstance(env_file, str | Path) and Path(env_file).is_file():
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
    if spec.kind == "watch_folders":
        return _check_watch_folders(spec, checked or [])
    return checked


def _check_watch_folders(spec: Spec, folders: list[WatchFolder]) -> list[dict[str, str]]:
    """Each folder exists and is given by its full path; no folder twice or inside another (its
    files would be watched twice)."""
    paths: list[Path] = []
    for folder in folders:
        path = folder.path.expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise SettingsError(
                "invalid",
                tr(
                    "{label}：找不到文件夹 {folder}，需填写已存在文件夹的完整路径",
                    label=tr(spec.label),
                    folder=folder.path,
                ),
            )
        resolved = path.resolve()
        for other in paths:
            if (
                resolved == other
                or resolved.is_relative_to(other)
                or other.is_relative_to(resolved)
            ):
                raise SettingsError(
                    "invalid",
                    tr(
                        "{label}：{folder} 与另一个监视文件夹重复或互相包含",
                        label=tr(spec.label),
                        folder=folder.path,
                    ),
                )
        paths.append(resolved)
    return [
        {"path": str(f.path.expanduser()), "template": f.template.strip() or "auto"}
        for f in folders
    ]


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
            if effective.jacara_desktop and key.upper() in RUNTIME:
                continue  # Fixed by the desktop app; nothing to choose.
            if spec.desktop_only and not effective.jacara_desktop:
                continue
            note = None
            if spec.needs_builds:
                from jacara.plugins.model_backends.llama_builds import device_note

                if (note := device_note()) is None:
                    continue
            value = getattr(effective, key)
            default = Settings.model_fields[key].default
            source = "env" if key in locked else "page" if key in saved else "default"
            if spec.kind == "watch_folders":
                # Includes the single folder of JACARA_WATCH_DIR (.env, or the page before there
                # were several).
                value = [
                    {"path": str(f.path), "template": f.template} for f in effective.watch_folders()
                ]
                if LEGACY & set(saved):
                    source = "page"
                if "jacara_watch_dir" in locked:
                    source = "env"
            items.append(
                SettingItem(
                    key=key,
                    group=tr(spec.group),
                    label=tr(spec.label),
                    help=tr(spec.help) + (f" {note}" if note else ""),
                    kind=spec.kind,  # type: ignore[arg-type]
                    value=str(value) if isinstance(value, Path) else value,
                    default=str(default) if isinstance(default, Path) else default,
                    source=source,  # type: ignore[arg-type]
                    env=key.upper(),
                    section=spec.section,  # type: ignore[arg-type]
                    live=spec.live,
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

    def _check_template(self, key: str, value: str) -> None:
        """A template setting takes the values of the upload page: auto, discover, or one of my
        templates that is not archived."""
        from jacara.modules.templates.service import TemplateFailure, TemplateService

        try:
            TemplateService(self.owner, self.session).pick_for_upload(value)
        except TemplateFailure as err:
            raise SettingsError(
                "invalid", tr("{label}：{reason}", label=tr(SPECS[key].label), reason=err.message)
            ) from err

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
                if SPECS[key].kind == "template":
                    self._check_template(key, changed[key])
                if SPECS[key].kind == "watch_folders":
                    for folder in changed[key]:
                        self._check_template(key, folder["template"])
            if SPECS[key].kind == "watch_folders":
                # The list replaces the single folder kept from before.
                for legacy in LEGACY:
                    if saved.pop(legacy, None) is not None:
                        changed[legacy] = None
        if not changed:
            return self.get()
        path = config.page_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # Only changes that the background services read at start ask for a restart.
        needs_restart = any(not SPECS[k].live for k in changed if k in SPECS)
        saved_at = time.time() if needs_restart else _saved_at()
        body = {**saved, "_saved_at": saved_at, "_saved": datetime.now(UTC).isoformat()}
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), "utf-8")
        # Write to a temporary file and rename, so that a power loss never leaves a partially
        # written file.
        tmp.replace(path)
        get_settings.cache_clear()
        record(self.session, self.owner, "settings:update", None, None, **changed)
        return self.get()
