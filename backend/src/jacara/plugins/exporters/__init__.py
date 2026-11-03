"""导出插件登记表：按名字创建导出器。

新增一种格式（例如 CSV）：在本目录新建 csv.py 实现 Exporter，再在 _EXPORTERS 里加一行。
"""

from collections.abc import Callable

from jacara.plugins.exporters.base import DocumentReport, Exporter
from jacara.plugins.exporters.excel import ExcelExporter

_EXPORTERS: dict[str, Callable[[], Exporter]] = {
    "excel": ExcelExporter,
}


def available_exporters() -> list[str]:
    """列出所有登记过的导出格式。"""
    return sorted(_EXPORTERS)


def create_exporter(name: str) -> Exporter:
    """按名字创建导出器。名字没登记过就报错，并列出可用的名字。"""
    factory = _EXPORTERS.get(name)
    if factory is None:
        raise ValueError(f"未知的导出格式：{name}，可用的有：{available_exporters()}")
    return factory()


__all__ = ["DocumentReport", "ExcelExporter", "Exporter", "available_exporters", "create_exporter"]
