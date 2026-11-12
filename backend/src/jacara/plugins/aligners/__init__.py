"""证据对齐插件：按文字块核对（BlockAligner，ADR 0034），
核对不上时按文字在整页找（TextMatchAligner）。"""

from jacara.plugins.aligners.base import AlignedField, Aligner, AlignStatus, Evidence
from jacara.plugins.aligners.blocks import BlockAligner
from jacara.plugins.aligners.text_match import TextMatchAligner

__all__ = [
    "AlignStatus",
    "AlignedField",
    "Aligner",
    "BlockAligner",
    "Evidence",
    "TextMatchAligner",
]
