"""文档接口：/api/v1/documents。

router 只做三件事：读请求、调用 service、把结果或错误变成 HTTP 响应。不碰数据库。
"""

from __future__ import annotations

import uuid
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_queue, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.schemas import DocumentDetail, DocumentOut, DocumentPage
from jacara.modules.documents.service import DocumentError, DocumentService

router = APIRouter(prefix="/documents", tags=["文档"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP = "application/zip"

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "no_file": status.HTTP_404_NOT_FOUND,
    "unsupported": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "empty": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "unreadable": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
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


def _attachment(filename: str, data: bytes, media_type: str) -> Response:
    """让浏览器把响应当成文件下载，并用 filename 作为文件名。

    文件名可能有中文：filename* 按标准（RFC 6266）写成 UTF-8 编码；
    filename 是给不认识 filename* 的老程序看的纯英文名字。
    """
    fallback = f"export{PurePath(filename).suffix}"  # 纯英文的备用名：export.xlsx、export.zip
    disposition = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data, media_type=media_type, headers={"Content-Disposition": disposition}
    )


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


@router.get("/export.xlsx", summary="导出多个文档", response_class=Response)
def export_documents(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
) -> Response:
    """把多个文档导出到一个 Excel：汇总表一行一张单据，方便对账。

    写法：`/api/v1/documents/export.xlsx?ids=第一个id&ids=第二个id`
    """
    try:
        filename, data = service.export(ids)
    except DocumentError as err:
        raise _http_error(err) from err
    return _attachment(filename, data, XLSX)


@router.get("/export.zip", summary="导出多个文档（每个一个 Excel）", response_class=Response)
def export_documents_zip(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
) -> Response:
    """每个文档单独一个 Excel，打包成一个 zip 下载，解压后就是一个文件夹。

    写法：`/api/v1/documents/export.zip?ids=第一个id&ids=第二个id`
    """
    try:
        filename, data = service.export_zip(ids)
    except DocumentError as err:
        raise _http_error(err) from err
    return _attachment(filename, data, ZIP)


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


@router.get("/{document_id}/pages/{page}/image", summary="某一页的图片", response_class=Response)
def page_image(
    document_id: uuid.UUID, page: Annotated[int, Path(ge=1)], service: Service
) -> Response:
    """第 page 页（从 1 开始）画成 PNG。审核页面显示它，并按字段的 evidence.bbox 在上面画出处框。

    PDF 按抽取时同样的倍数渲染，图片按原像素输出，所以坐标能对上。
    """
    try:
        data = service.page_image(document_id, page)
    except DocumentError as err:
        raise _http_error(err) from err
    # 同一页的图片不会变，浏览器可以缓存一小时；private：只存在自己的浏览器里
    return Response(
        content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/{document_id}/export.xlsx", summary="导出 Excel", response_class=Response)
def export_document(document_id: uuid.UUID, service: Service) -> Response:
    """把一个文档导出成 Excel。格子里是审核之后的值，改过的标蓝，批注里有原值和原因。"""
    try:
        filename, data = service.export([document_id])
    except DocumentError as err:
        raise _http_error(err) from err
    return _attachment(filename, data, XLSX)


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
