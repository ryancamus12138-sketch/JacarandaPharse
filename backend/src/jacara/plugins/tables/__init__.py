"""Table recognition plugins (reserved). Only the interface exists; no engine is registered yet."""

from jacara.plugins.tables.base import Table, TableCell, TableConfig, TableEngine, TableResult

__all__ = ["Table", "TableCell", "TableConfig", "TableEngine", "TableResult"]
