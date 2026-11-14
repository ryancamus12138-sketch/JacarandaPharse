"""设置页接口收发的 JSON（ADR 0063）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Option(BaseModel):
    value: str
    label: str


class SettingItem(BaseModel):
    key: str
    """和 .env 里的变量名对应（小写），例如 jacara_vision ↔ JACARA_VISION。"""
    group: str
    label: str
    help: str
    kind: Literal["choice", "bool", "int", "path", "text"]
    value: Any
    default: Any
    source: Literal["env", "page", "default"]
    """env：.env 或环境变量指定（页面上改不了）；page：设置页上改的；default：默认值。"""
    env: str
    """.env 里的变量名。"""
    options: list[Option] = []
    min: int | None = None
    max: int | None = None


class SettingsOut(BaseModel):
    items: list[SettingItem]
    can_change: bool
    restart_needed: bool
    """页面上改过、还没 jacara stop、jacara start：后台还在用旧的。"""


class SettingsIn(BaseModel):
    """要改的：键是 SettingItem.key；值是 null 表示改回默认值。不写的不变。"""

    values: dict[str, Any]
