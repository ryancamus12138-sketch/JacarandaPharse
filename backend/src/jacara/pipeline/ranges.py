"""日期范围字段的导出设置（ADR 0039）：从模板里取出来交给导出器。"""

from __future__ import annotations

from jacara.plugins.exporters.base import RangeSetting
from jacara.templates import Template


def range_settings(template: Template | None) -> dict[str, RangeSetting]:
    """字段 ID（表格的列写成 items.period）→ 导出成什么样、分隔符、数字日期的日月顺序。"""
    if template is None:
        return {}
    return {
        name: RangeSetting(f.range_export, f.range_separator, template.day_first)
        for name, f in template.ranges().items()
    }
