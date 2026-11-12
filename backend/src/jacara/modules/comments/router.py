"""备注接口：/api/v1/documents/{id}/comments、/api/v1/comments/{id}。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session
from jacara.core.owner import Owner
from jacara.modules.comments.schemas import CommentIn, CommentOut
from jacara.modules.comments.service import CommentError, CommentService

document_comments_router = APIRouter(prefix="/documents", tags=["备注"])
comments_router = APIRouter(prefix="/comments", tags=["备注"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "forbidden": status.HTTP_403_FORBIDDEN,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> CommentService:
    return CommentService(session, owner)


Service = Annotated[CommentService, Depends(get_service)]


def _http_error(err: CommentError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


@document_comments_router.get("/{document_id}/comments", summary="文档的备注")
def list_comments(document_id: uuid.UUID, service: Service) -> list[CommentOut]:
    """这份文档上的全部备注（整份的和字段上的），先写的在前。"""
    try:
        return service.list(document_id)
    except CommentError as err:
        raise _http_error(err) from err


@document_comments_router.post(
    "/{document_id}/comments", status_code=status.HTTP_201_CREATED, summary="写备注"
)
def add_comment(document_id: uuid.UUID, data: CommentIn, service: Service) -> CommentOut:
    """`{"body": "这张要问财务"}` 写在整份文档上；加 `"field_name": "total"` 写在某个字段上。"""
    try:
        return service.add(document_id, data)
    except CommentError as err:
        raise _http_error(err) from err


@comments_router.post("/{comment_id}/resolve", summary="标为已解决")
def resolve_comment(comment_id: uuid.UUID, service: Service) -> CommentOut:
    try:
        return service.resolve(comment_id, resolved=True)
    except CommentError as err:
        raise _http_error(err) from err


@comments_router.post("/{comment_id}/reopen", summary="重新打开")
def reopen_comment(comment_id: uuid.UUID, service: Service) -> CommentOut:
    try:
        return service.resolve(comment_id, resolved=False)
    except CommentError as err:
        raise _http_error(err) from err


@comments_router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除备注")
def delete_comment(comment_id: uuid.UUID, service: Service) -> Response:
    """只有写的人或管理员能删。"""
    try:
        service.delete(comment_id)
    except CommentError as err:
        raise _http_error(err) from err
    return Response(status_code=status.HTTP_204_NO_CONTENT)
