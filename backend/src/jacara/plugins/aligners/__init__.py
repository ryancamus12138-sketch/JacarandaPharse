"""Evidence alignment plugins: block-based verification (BlockAligner), falling back to a full-page
text search (TextMatchAligner)."""

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
