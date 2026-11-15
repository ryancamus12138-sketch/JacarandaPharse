"""Comment persistence. All queries filter by team_id."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara.models import Comment, Profile, User


class CommentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, comment: Comment) -> Comment:
        self.session.add(comment)
        self.session.flush()
        return comment

    def get(self, comment_id: uuid.UUID, team_id: uuid.UUID) -> Comment | None:
        return self.session.scalars(
            select(Comment).where(Comment.id == comment_id, Comment.team_id == team_id)
        ).one_or_none()

    def of_document(self, document_id: uuid.UUID, team_id: uuid.UUID) -> list[Comment]:
        """All comments of a document, oldest first."""
        query = (
            select(Comment)
            .where(Comment.document_id == document_id, Comment.team_id == team_id)
            .order_by(Comment.created_at, Comment.id)
        )
        return list(self.session.scalars(query))

    def open_counts(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Number of unresolved comments per document (shown in lists)."""
        if not document_ids:
            return {}
        rows = self.session.execute(
            select(Comment.document_id, func.count())
            .where(Comment.document_id.in_(document_ids), Comment.resolved_at.is_(None))
            .group_by(Comment.document_id)
        )
        return {doc: count for doc, count in rows}

    def delete(self, comment: Comment) -> None:
        self.session.delete(comment)

    def names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        """User id → display name (display name → user name; personal-mode display names are in
        profiles)."""
        if not user_ids:
            return {}
        names: dict[uuid.UUID, str] = {}
        for profile in self.session.scalars(select(Profile).where(Profile.user_id.in_(user_ids))):
            if profile.display_name:
                names[profile.user_id] = profile.display_name
        for user in self.session.scalars(select(User).where(User.id.in_(user_ids))):
            names[user.id] = user.display_name or user.username
        return names
