"""Comments: notes left for colleagues during review ("Check with finance"), attached to a whole
document or to a field.

Anyone who can view the document can read, write and resolve comments; only the author or an
administrator can delete them. The audit log records only who added or deleted a comment on which
document, never its content."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jacara.core.i18n import tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.models import Comment, Document
from jacara.modules.audit.service import record
from jacara.modules.comments.repository import CommentRepository
from jacara.modules.comments.schemas import CommentIn, CommentOut


class CommentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommentService:
    def __init__(self, session: Session, owner: Owner) -> None:
        self.session = session
        self.owner = owner
        self.repo = CommentRepository(session)

    def list(self, document_id: uuid.UUID) -> list[CommentOut]:
        document = self._document(document_id)
        return self._out(self.repo.of_document(document.id, document.team_id))

    def add(self, document_id: uuid.UUID, data: CommentIn) -> CommentOut:
        document = self._document(document_id)
        body = data.body.strip()
        if not body:
            raise CommentError("invalid", tr("备注不能为空"))
        field = (data.field_name or "").strip() or None
        page = data.page if field else None
        if field and page is None:
            raise CommentError("invalid", tr("字段备注需指定页码"))
        if page is not None and page > document.page_count:
            raise CommentError(
                "invalid", tr("此文档仅有 {page_count} 页", page_count=document.page_count)
            )
        comment = self.repo.add(
            Comment(
                document_id=document.id,
                page=page,
                field_name=field,
                body=body,
                user_id=self.owner.user_id,
                team_id=document.team_id,
            )
        )
        record(
            self.session,
            self.owner,
            "comment:create",
            "document",
            document.id,
            comment=str(comment.id),
            field=comment.field_name or "",
        )
        return self._out([comment])[0]

    def resolve(self, comment_id: uuid.UUID, resolved: bool) -> CommentOut:
        comment = self._find(comment_id)
        if resolved and comment.resolved_at is None:
            comment.resolved_at = datetime.now(UTC)
            comment.resolved_by = self.owner.user_id
        elif not resolved:
            comment.resolved_at = None
            comment.resolved_by = None
        action = "comment:resolve" if resolved else "comment:reopen"
        record(
            self.session,
            self.owner,
            action,
            "document",
            comment.document_id,
            comment=str(comment.id),
        )
        self.session.flush()
        return self._out([comment])[0]

    def delete(self, comment_id: uuid.UUID) -> None:
        comment = self._find(comment_id)
        if comment.user_id != self.owner.user_id and self.owner.role != "admin":
            raise CommentError("forbidden", tr("仅备注作者或管理员可删除"))
        record(
            self.session,
            self.owner,
            "comment:delete",
            "document",
            comment.document_id,
            comment=str(comment.id),
        )
        self.repo.delete(comment)

    # ---- Internal ----

    def _document(self, document_id: uuid.UUID) -> Document:
        document = self.session.get(Document, document_id)
        if document is None or document.team_id != self.owner.team_id:
            raise CommentError("not_found", tr("文档不存在"))
        require(self.owner, "document:read", document.team_id)
        return document

    def _find(self, comment_id: uuid.UUID) -> Comment:
        comment = self.repo.get(comment_id, self.owner.team_id)
        if comment is None:
            raise CommentError("not_found", tr("备注不存在"))
        require(self.owner, "document:read", comment.team_id)
        return comment

    def _out(self, comments: list[Comment]) -> list[CommentOut]:
        ids = {c.user_id for c in comments} | {c.resolved_by for c in comments if c.resolved_by}
        names = self.repo.names(ids)
        return [
            CommentOut(
                id=c.id,
                document_id=c.document_id,
                page=c.page,
                field_name=c.field_name,
                body=c.body,
                author=names.get(c.user_id, tr("本机用户")),
                mine=c.user_id == self.owner.user_id,
                created_at=c.created_at or datetime.now(UTC),
                resolved_at=c.resolved_at,
                resolved_by=names.get(c.resolved_by, tr("本机用户")) if c.resolved_by else None,
            )
            for c in comments
        ]
