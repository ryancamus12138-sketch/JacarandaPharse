"""Field value persistence. Only inserts revisions; no update or delete methods exist."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from jacara.models import ExtractedValue, Extraction, ValueRevision


class ValueRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, value_id: uuid.UUID, team_id: uuid.UUID) -> ExtractedValue | None:
        """Load a field with its revisions and its document (whose status matters)."""
        query = (
            select(ExtractedValue)
            .where(ExtractedValue.id == value_id, ExtractedValue.team_id == team_id)
            .options(
                selectinload(ExtractedValue.revisions),
                selectinload(ExtractedValue.extraction).selectinload(Extraction.document),
            )
        )
        return self.session.scalars(query).one_or_none()

    def get_extraction(self, extraction_id: uuid.UUID, team_id: uuid.UUID) -> Extraction | None:
        """Load the extraction of one page with all fields, revisions and the document (adding or
        removing rows needs the whole table)."""
        query = (
            select(Extraction)
            .where(Extraction.id == extraction_id, Extraction.team_id == team_id)
            .options(
                selectinload(Extraction.values).selectinload(ExtractedValue.revisions),
                selectinload(Extraction.document),
            )
        )
        return self.session.scalars(query).one_or_none()

    def add_revision(self, revision: ValueRevision) -> ValueRevision:
        self.session.add(revision)
        self.session.flush()  # Flush immediately: a duplicate seq (concurrent edits) fails here.
        return revision
