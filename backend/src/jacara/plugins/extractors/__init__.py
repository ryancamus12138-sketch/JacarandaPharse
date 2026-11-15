"""Field extraction plugins. Currently only the LLM extractor; a registry will be added with a
second extractor.

The fields to extract are defined by templates (backend/src/jacara/templates/), not here."""

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    Extractor,
    FieldSpec,
    FieldType,
    TableSpec,
    cell_name,
    split_cell_name,
)
from jacara.plugins.extractors.llm import PROMPT_VERSION, LlmExtractor, build_schema

__all__ = [
    "PROMPT_VERSION",
    "ExtractedField",
    "ExtractionError",
    "ExtractionResult",
    "Extractor",
    "FieldSpec",
    "FieldType",
    "LlmExtractor",
    "TableSpec",
    "build_schema",
    "cell_name",
    "split_cell_name",
]
