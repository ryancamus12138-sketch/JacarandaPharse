"""字段抽取插件。目前只有大模型抽取器；有第二种抽取器时再加登记表。"""

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    Extractor,
    FieldSpec,
    FieldType,
)
from jacara.plugins.extractors.llm import PROMPT_VERSION, LlmExtractor, build_schema
from jacara.plugins.extractors.presets import PRESETS, RECEIPT_FIELDS, RECEIPT_FULL_FIELDS

__all__ = [
    "PRESETS",
    "PROMPT_VERSION",
    "RECEIPT_FIELDS",
    "RECEIPT_FULL_FIELDS",
    "ExtractedField",
    "ExtractionError",
    "ExtractionResult",
    "Extractor",
    "FieldSpec",
    "FieldType",
    "LlmExtractor",
    "build_schema",
]
