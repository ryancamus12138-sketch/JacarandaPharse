"""Request and response schemas of the settings API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Option(BaseModel):
    value: str
    label: str


class SettingItem(BaseModel):
    key: str
    """Matches the variable name in .env, lowercased; for example jacara_vision ↔ JACARA_VISION."""
    group: str
    label: str
    help: str
    kind: Literal["choice", "bool", "int", "path", "text", "template", "watch_folders"]
    value: Any
    default: Any
    source: Literal["env", "page", "default"]
    """env: set in .env or the environment (not editable on the page); page: set on the settings
    page; default: default value."""
    env: str
    """Variable name in .env."""
    options: list[Option] = []
    min: int | None = None
    max: int | None = None
    section: Literal["processing", "data"] = "processing"
    """Settings page that shows it: Recognition and processing, or Data and backups."""
    live: bool = False
    """Applies without restarting the background services."""


class SettingsOut(BaseModel):
    items: list[SettingItem]
    can_change: bool
    restart_needed: bool
    """Changed on the page but `jacara stop` / `jacara start` has not run yet: background workers
    still use the old value."""


class SettingsIn(BaseModel):
    """Changes: keys are SettingItem.key; a null value restores the default. Omitted keys are
    unchanged."""

    values: dict[str, Any]
