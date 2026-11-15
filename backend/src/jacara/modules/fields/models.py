"""Field value and review tables.

extracted_values   Every field extracted by the model: value, evidence, alignment status. Never
modified after insertion.
value_revisions    Manual review: each edit or confirmation inserts a row; rows are never updated
or deleted.

The current value is the value of the latest revision; without revisions it is the model value.

Rows of tables (line items) can be added and removed manually:
- Deleting a row inserts a revision with action="delete" for each cell instead of deleting;
  restoring inserts action="restore".
- Adding a row inserts fields with origin="manual" and empty values, filled through revisions.
- Display order follows sort_key: a row inserted between two rows takes the midpoint, so existing
  rows are not renumbered."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.documents.models import Extraction


class ExtractedValue(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "extracted_values"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id"), index=True)
    position: Mapped[int]
    """Position in the field list (fixed at extraction)."""
    sort_key: Mapped[float] = mapped_column(Float, server_default="0")
    """Display order. Equals position at extraction; manually inserted rows take the midpoint of
    their neighbors."""
    origin: Mapped[str] = mapped_column(String(16), default="model", server_default="model")
    """model: extracted by the model; manual: entered manually (added table rows, fields set to
    manual entry in the template); computed: calculated by a template formula and recalculated
    when its inputs change."""

    name: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(200))
    """Label on the document (in key-value discovery, the "Check-in" behind kv.3). Empty for
    template fields, whose names come from the template."""
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str | None] = mapped_column(Text)
    """Value extracted by the model. Stored as text (numbers too, e.g. "9.0") and restored by type;
    None means absent from the source."""
    rejected_value: Mapped[str | None] = mapped_column(Text)
    """Value given by the model but not found in the referenced text blocks or elsewhere in the
    source, and therefore not filled: the model value is kept here and value is empty. A reviewer
    can accept it with one click after checking the source."""
    row_no: Mapped[int | None]
    """Line number given by the model (not named row, which is an SQL keyword)."""

    status: Mapped[str] = mapped_column(String(16))
    """Alignment result: matched, fuzzy, not_found, empty."""
    needs_review: Mapped[bool]
    """Decided at extraction: whether this value requires confirmation. Never changes; see reviewed
    for whether it was handled."""
    note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any] | None]
    """Evidence: line number, line text, bounding box, OCR confidence, similarity."""
    candidates: Mapped[dict[str, Any] | None]
    """Candidates: {"items": [{source, text, value, page, box, confidence, note, dpi, rule_ok}]}.
    Differing values read by OCR, the PDF text layer, zoomed re-recognition and form fields,
    offered for selection and never applied automatically."""

    extraction: Mapped[Extraction] = relationship(back_populates="values")
    revisions: Mapped[list[ValueRevision]] = relationship(
        back_populates="original", order_by="ValueRevision.seq"
    )

    # The following are not columns but derived from revisions; load revisions before use.

    @property
    def current_value(self) -> str | None:
        """Current value: the value of the latest revision, or the original value if never
        reviewed."""
        return self.revisions[-1].new_value if self.revisions else self.value

    @property
    def current_evidence(self) -> dict[str, Any] | None:
        """Current evidence: the evidence recorded by the latest "select from source text",
        otherwise the evidence found at extraction.

                When only the value was typed, the evidence stays unchanged: the value no longer
        matches the source, and the revision record explains why."""
        for revision in reversed(self.revisions):
            if revision.evidence is not None:
                return revision.evidence
        return self.evidence

    @property
    def deleted(self) -> bool:
        """The row containing this cell was deleted (tables only)."""
        return bool(self.revisions) and self.revisions[-1].action == "delete"

    @property
    def reviewed(self) -> bool:
        """Edited or confirmed by a person. Automatic recalculation (action="compute") does not
        count as reviewed."""
        return any(r.action != "compute" for r in self.revisions)

    @property
    def pending_review(self) -> bool:
        """Awaiting confirmation: flagged at extraction and not yet handled."""
        return self.needs_review and not self.reviewed

    @property
    def evidence_state(self) -> str:
        """Evidence dimension, describing the evidence found at extraction; unaffected by review:

        - verified: found in the source text and identical to the value, without any
          doubt
        - pending: found, but needs a look (fuzzy match, wrong line number, low OCR
          confidence, wrong sign), or read from the page image
        - missing: not found in the source; possibly invented or rewritten by the model
          (includes values rejected by rule)
        - empty: the model reported the field as absent (also manually added cells)"""
        if self.status in ("not_found", "rejected"):
            return "missing"
        if self.status == "computed":
            return "verified"  # Computed: correctness depends on the input fields and rules.
        if self.status == "seen":
            # Read from the page image: not matched in OCR, so a reviewer should check it against
            # the image.
            return "pending"
        if self.status == "empty" or self.evidence is None:
            return "empty"
        if self.status == "fuzzy" or self.note:
            return "pending"
        return "verified"


class ValueRevision(Base, OwnedMixin, TimestampMixin):
    """A manual review: edit or confirm. Insert-only; never updated or deleted.

    user_id (from OwnedMixin) records the actor and created_at the time."""

    __tablename__ = "value_revisions"
    __table_args__ = (UniqueConstraint("value_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    value_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extracted_values.id"), index=True)
    seq: Mapped[int] = mapped_column(server_default="1")
    """Sequence number of reviews of this field: 1, 2, 3, … When two users edit the same field
    concurrently, only one succeeds."""
    action: Mapped[str] = mapped_column(String(16), server_default="edit")
    """edit, confirm (the current value is correct), delete (delete this row), restore (restore this
    row), compute (recalculated by formula after an input changed; user_id is the user who
    changed the input)."""
    new_value: Mapped[str | None] = mapped_column(Text)
    """Value after this review. For confirmations, the confirmed value, so each row is complete on
    its own."""
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any] | None]
    """New evidence when selected from source text (same format as extracted_values.evidence);
    absent for typed values."""

    original: Mapped[ExtractedValue] = relationship(back_populates="revisions")
