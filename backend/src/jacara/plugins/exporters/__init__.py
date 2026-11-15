"""Exporter registry: create exporters by name.

To add a format: implement Exporter in a new module in this package and add an entry to
_EXPORTERS."""

from collections.abc import Callable

from jacara.core.i18n import join_items, stored
from jacara.plugins.exporters.base import DocumentReport, Exporter, Review
from jacara.plugins.exporters.excel import ExcelExporter

_EXPORTERS: dict[str, Callable[[], Exporter]] = {
    "excel": ExcelExporter,
}


def available_exporters() -> list[str]:
    """All registered export formats."""
    return sorted(_EXPORTERS)


def create_exporter(name: str) -> Exporter:
    """Create an exporter by name. Raises for unregistered names, listing the available ones."""
    factory = _EXPORTERS.get(name)
    if factory is None:
        raise ValueError(
            stored(
                "未知的导出格式：{name}，可用格式：{available}",
                name=name,
                available=join_items(available_exporters()),
            )
        )
    return factory()


__all__ = [
    "DocumentReport",
    "ExcelExporter",
    "Exporter",
    "Review",
    "available_exporters",
    "create_exporter",
]
