"""字段抽取插件。目前只有大模型抽取器；有第二种抽取器时再加登记表。

抽哪些字段不写在这里，由模板决定（backend/src/jacara/templates/）。
"""

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    Extractor,
    FieldSpec,
    FieldType,
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
    "build_schema",
]
