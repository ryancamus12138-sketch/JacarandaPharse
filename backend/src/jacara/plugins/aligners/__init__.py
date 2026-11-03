"""证据对齐插件。目前只有文字比对对齐器；有第二种时再加登记表。"""

from jacara.plugins.aligners.base import AlignedField, Aligner, AlignStatus, Evidence
from jacara.plugins.aligners.text_match import TextMatchAligner

__all__ = ["AlignStatus", "AlignedField", "Aligner", "Evidence", "TextMatchAligner"]
