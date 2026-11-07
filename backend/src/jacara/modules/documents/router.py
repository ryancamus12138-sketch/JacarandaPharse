"""文档接口：/api/v1/documents。

router 只做三件事：读请求、调用 service、把结果或错误变成 HTTP 响应。不碰数据库。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_queue, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.schemas import DocumentDetail, DocumentOut, DocumentPage
from jacara.modules.documents.service import DocumentError, DocumentService

router = APIRouter(prefix="/documents", tags=["文档"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "no_file": status.HTTP_404_NOT_FOUND,
    "unsupported": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "empty": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "unreadable": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "conflict": status.HTTP_409_CONFLICT,
    "storage": status.HTTP_503_SERVICE_UNAVAILABLE,
    "queue": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    queue: Annotated[TaskQueue, Depends(get_queue)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> DocumentService:
    return DocumentService(session, storage, queue, owner)


Service = Annotated[DocumentService, Depends(get_service)]


def _http_error(err: DocumentError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


@router.post("", status_code=status.HTTP_201_CREATED, summary="上传文件")
def upload_document(file: UploadFile, service: Service) -> DocumentOut:
    """上传一张图片或一个 PDF。文件存进对象存储，文档信息存进数据库，放进后台队列等待处理。

    马上返回，状态为 uploaded。之后用"文档详情"查看状态，变成 done 就有抽取结果了。
    """
    data = file.file.read()
    try:
        return service.upload(file.filename or "", file.content_type or "", data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("", summary="文档列表")
def list_documents(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    """按上传时间倒序。limit 是每页几条，offset 是跳过几条（翻页用）。"""
    return service.page(limit, offset)


@router.get("/{document_id}", summary="文档详情")
def get_document(document_id: uuid.UUID, service: Service) -> DocumentDetail:
    """文档信息，加上每一页抽出的字段、出处和规则校验结果。"""
    try:
        return service.get(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/file", summary="下载原始文件", response_class=Response)
def download_document(document_id: uuid.UUID, service: Service) -> Response:
    """返回上传时的原文件。审核页面用它显示原图。"""
    try:
        document, data = service.download(document_id)
    except DocumentError as err:
        raise _http_error(err) from err
    return Response(content=data, media_type=document.content_type or "application/octet-stream")


@router.post("/{document_id}/review", summary="完成审核")
def review_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """所有标黄的字段都修改或确认过之后，把文档标记为已审核（reviewed）。

    还有字段没处理时返回 409，并说明还剩几个。
    """
    try:
        return service.review(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/process", status_code=status.HTTP_202_ACCEPTED, summary="重新处理")
def process_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """把失败（failed）或还在排队（uploaded）的文档重新放进后台队列。

    202 的意思是"收到了，会去做"，不是"已经做完了"。
    """
    try:
        return service.process(document_id)
    except DocumentError as err:
        raise _http_error(err) from err
