"""Export settings of date range fields, taken from the template and passed to exporters."""

from __future__ import annotations

from jacara.plugins.exporters.base import RangeSetting
from jacara.templates import Template


def range_settings(template: Template | None) -> dict[str, RangeSetting]:
    """Field ID (table columns as items.period) → export format, separator, and day/month order of
    numeric dates."""
    if template is None:
        return {}
    return {
        name: RangeSetting(f.range_export, f.range_separator, template.day_first)
        for name, f in template.ranges().items()
    }
