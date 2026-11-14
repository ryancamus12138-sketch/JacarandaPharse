"""设置页（ADR 0063）：.env 优先，其次是页面上改的（data/settings.json），再是默认值。

后台 worker 启动时读一次配置，所以改完要 jacara stop、jacara start；API 这边改完马上清掉缓存。
"""

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
from jacara.core.owner import Owner
from jacara.core.permissions import allowed, require
from jacara.modules.app_settings.schemas import Option, SettingItem, SettingsIn, SettingsOut
from jacara.modules.audit.service import record

STARTED = time.time()
"""这个进程是什么时候启动的：页面设置比它新，说明还没重启。"""


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
        "识别",
        "让模型看原图",
        "照片、扫描件 OCR 认不准时，让视觉模型看着原图读（ADR 0038）。要先放好视觉模型。",
        "choice",
        [("auto", "OCR 不可靠时才看（推荐）"), ("always", "每张都看（慢）"), ("off", "不看")],
    ),
    "jacara_pdf_read": Spec(
        "识别",
        "PDF 怎么读文字",
        "画成图做 OCR，和照片走同一条路，PDF 自带的文字只拿来核对（ADR 0042）；"
        "只读自带文字快，但只适合确定干净的电子版。",
        "choice",
        [("image", "画成图做 OCR（推荐）"), ("text", "只读 PDF 自带的文字")],
    ),
    "jacara_ocr_max_side": Spec(
        "识别",
        "OCR 时图片长边最多多少像素",
        "超过的先缩小。大一点小字认得更准，但更慢（ADR 0042）。",
        "int",
        min=1000,
        max=8000,
    ),
    "jacara_layout": Spec(
        "识别",
        "先找页面上的区域",
        "抽取前用版面模型找出标题、正文、表格、页眉页脚（ADR 0034）。",
        "bool",
    ),
    "jacara_learn": Spec(
        "从人改过的地方学",
        "抽取时带上以前人改过的例子",
        "同一个模板以前人工改过的值告诉模型，少犯同样的错（ADR 0045）。",
        "bool",
    ),
    "jacara_learn_mode": Spec(
        "从人改过的地方学",
        "怎么挑例子",
        "整理过的：只用错在模型的、合并重复、去掉过期的（ADR 0055）。换之前先用回放比一比。",
        "choice",
        [("recent", "最近改得多的（默认）"), ("curated", "整理过的")],
    ),
    "jacara_pipeline": Spec(
        "后台和导入",
        "后台分两个工位",
        "OCR 和模型同时干活，批量导入更快；关掉省几百 MB 内存（ADR 0051）。",
        "bool",
    ),
    "jacara_watch_dir": Spec(
        "后台和导入",
        "监视文件夹",
        "放进去的图片和 PDF 自动导入，不动原文件，重复的跳过（ADR 0036）。"
        "写完整路径；空着就不监视。",
        "path",
    ),
    "jacara_watch_template": Spec(
        "后台和导入",
        "监视文件夹用的模板",
        "我的模板的 id；空着就用上一次上传时选的。",
        "text",
    ),
    "max_upload_mb": Spec(
        "后台和导入",
        "上传文件最大多少 MB",
        "一个文件超过这么大就不收。",
        "int",
        min=1,
        max=500,
    ),
    "jacara_api_docs": Spec(
        "安全",
        "打开接口文档页 /docs",
        "开发时有用；给别人用时建议关掉，少暴露一些接口细节。",
        "bool",
    ),
}
if set(SPECS) != set(PAGE_EDITABLE):  # 两边要一起改
    raise RuntimeError("SPECS 和 config.PAGE_EDITABLE 对不上")


class SettingsError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def env_locked() -> set[str]:
    """环境变量或 .env 里写了的设置（这些页面上改不了）。只看有没有这个名字，不读值。"""
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
    """按 Settings 里这一项的类型检查，再按设置页的范围检查。返回能写进 JSON 的值。"""
    spec = SPECS[key]
    annotation: Any = Settings.model_fields[key].annotation
    try:
        checked = TypeAdapter(annotation).validate_python(value)
    except ValidationError as err:
        raise SettingsError("invalid", f"{spec.label}：值不对") from err
    if spec.kind == "int" and (
        (spec.min is not None and checked < spec.min)
        or (spec.max is not None and checked > spec.max)
    ):
        raise SettingsError("invalid", f"{spec.label}：要在 {spec.min}～{spec.max} 之间")
    if spec.kind == "path" and checked is not None:
        path = Path(checked).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise SettingsError("invalid", f"{spec.label}：要写一个已经存在的文件夹的完整路径")
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
                    group=spec.group,
                    label=spec.label,
                    help=spec.help,
                    kind=spec.kind,  # type: ignore[arg-type]
                    value=str(value) if isinstance(value, Path) else value,
                    default=str(default) if isinstance(default, Path) else default,
                    source="env" if key in locked else "page" if key in saved else "default",
                    env=key.upper(),
                    options=[Option(value=v, label=label) for v, label in spec.options],
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
                raise SettingsError("invalid", f"设置页不能改 {key}")
            if key in locked:
                raise SettingsError(
                    "invalid", f"{SPECS[key].label}由 .env 里的 {key.upper()} 指定，页面上改不了"
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
        tmp.replace(path)  # 先写临时文件再改名：写到一半断电也不会留下半个文件
        get_settings.cache_clear()
        record(self.session, self.owner, "settings:update", None, None, **changed)
        return self.get()
