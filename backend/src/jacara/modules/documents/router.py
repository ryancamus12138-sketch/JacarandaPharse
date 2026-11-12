"""文档接口：/api/v1/documents。

router 只做三件事：读请求、调用 service、把结果或错误变成 HTTP 响应。不碰数据库。
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_ocr, get_owner, get_queue, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.repository import DocumentFilter
from jacara.modules.documents.schemas import (
    Dashboard,
    DocumentDetail,
    DocumentNeighbors,
    DocumentOut,
    DocumentPage,
    FolderOut,
    PageOcr,
    PageStructureOut,
    RegionIn,
    RegionOut,
    ReprocessIn,
    SkipIn,
)
from jacara.modules.documents.service import DocumentError, DocumentService, day_start
from jacara.modules.fields.schemas import AdoptOut
from jacara.plugins.ocr.base import OcrEngine

router = APIRouter(prefix="/documents", tags=["文档"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["文档"])

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
    "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "conflict": status.HTTP_409_CONFLICT,
    "storage": status.HTTP_503_SERVICE_UNAVAILABLE,
    "queue": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    queue: Annotated[TaskQueue, Depends(get_queue)],
    owner: Annotated[Owner, Depends(get_owner)],
    ocr: Annotated[OcrEngine, Depends(get_ocr)],
) -> DocumentService:
    return DocumentService(session, storage, queue, owner, ocr)


Service = Annotated[DocumentService, Depends(get_service)]


def _http_error(err: DocumentError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


def attachment(filename: str, data: bytes, media_type: str) -> Response:
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
def upload_document(
    file: UploadFile,
    service: Service,
    template: Annotated[str | None, Form()] = None,
    folder: Annotated[str | None, Form(max_length=1000)] = None,
    split: Annotated[str, Form(max_length=200)] = "auto",
    skip_duplicate: Annotated[bool, Form()] = False,
) -> DocumentOut:
    """上传一张图片或一个 PDF。文件存进对象存储，文档信息存进数据库，放进后台队列等待处理。

    template：我的模板的 id，或者 discover（不用模板，抽出所有键值对）。不写：用上一次用的。
    马上返回，状态为 uploaded。之后用"文档详情"查看状态，变成 done 就有抽取结果了。

    批量导入（ADR 0036）：folder 是文件所在的文件夹（列表按它筛选）；split 是文件里有几张单据
    （auto 自动判断、one 整个文件一张、pages 每页一张，或"1-2, 3, 4-6"）；
    skip_duplicate=true 时，同一个文件之前传过就不新建，返回之前那份（duplicate_skipped=true）。
    """
    data = file.file.read()
    try:
        return service.upload(
            file.filename or "",
            file.content_type or "",
            data,
            template,
            folder=folder,
            split=split,
            skip_duplicate=skip_duplicate,
        )
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("", summary="文档列表")
def list_documents(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_: Annotated[
        list[str] | None, Query(alias="status", description="状态，可以写多个")
    ] = None,
    template: Annotated[str | None, Query(description="用哪个模板抽取的")] = None,
    date_from: Annotated[date | None, Query(description="上传日期从（含）")] = None,
    date_to: Annotated[date | None, Query(description="上传日期到（含）")] = None,
    q: Annotated[str, Query(max_length=100, description="文件名里包含")] = "",
    pending: Annotated[bool, Query(description="只看还有字段等人确认的")] = False,
    value_q: Annotated[
        str, Query(max_length=100, description="字段内容里包含（例如发票号码、商家名）")
    ] = "",
    field: Annotated[
        str | None, Query(max_length=64, description="和 value_q 一起用：只在这个字段里找")
    ] = None,
    skipped: Annotated[
        bool | None, Query(description="true 只看跳过的，false 不看跳过的，不填都看")
    ] = None,
    folder: Annotated[
        str | None, Query(max_length=255, description="批量导入的文件夹（含下面的子文件夹）")
    ] = None,
) -> DocumentPage:
    """按上传时间倒序。limit 是每页几条，offset 是跳过几条（翻页用）。

    筛选：`?status=done&status=failed&template=receipt&date_from=2026-11-01&q=costco&pending=true`。
    按字段内容找：`?value_q=044031900111&field=invoice_no`（不填 field 就在所有字段里找）。
    日期按这台电脑的时区算。
    """
    filters = DocumentFilter(
        statuses=tuple(status_ or ()),
        template=template or None,
        since=day_start(date_from) if date_from else None,
        until=day_start(date_to + timedelta(days=1)) if date_to else None,
        search=q,
        pending_only=pending,
        value_search=value_q,
        field=field or None,
        skipped=skipped,
        folder=folder or None,
    )
    return service.page(limit, offset, filters)


@router.get("/folders", summary="批量导入的文件夹")
def list_folders(service: Service) -> list[FolderOut]:
    """批量导入时的文件夹和每个文件夹里有几份文档（列表按文件夹筛选用）。"""
    return service.folders()


@dashboard_router.get("", summary="工作台")
def dashboard(service: Service) -> Dashboard:
    """首页的统计和待办：各状态多少份、今天处理和审核了多少、处理队列（带进度）、需要先处理的。"""
    return service.dashboard()


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
    return attachment(filename, data, XLSX)


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
    return attachment(filename, data, ZIP)


@router.get("/{document_id}", summary="文档详情")
def get_document(
    document_id: uuid.UUID,
    service: Service,
    run: Annotated[int | None, Query(ge=1)] = None,
) -> DocumentDetail:
    """文档信息，加上每一页抽出的字段、出处和规则校验结果。

    run：看第几轮处理的结果（旧的几轮只能看）。不写就是现在用的这一轮。
    """
    try:
        return service.get(document_id, run)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/neighbors", summary="上一份、下一份")
def document_neighbors(document_id: uuid.UUID, service: Service) -> DocumentNeighbors:
    """按列表顺序（上传时间倒序），这份前后两份待审核的文档。审核完一份直接跳到下一份。"""
    try:
        return service.neighbors(document_id)
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


@router.get("/{document_id}/pages/{page}/structure", summary="某一页的结构")
def page_structure(
    document_id: uuid.UUID,
    page: Annotated[int, Path(ge=1)],
    service: Service,
    run: Annotated[int | None, Query(ge=1)] = None,
) -> PageStructureOut:
    """这一页认出的版面区域、表格的格子（第几行第几列）、"标题 → 值"（ADR 0034）。

    审核页"版面结构"在原图上画出来。2026-11-14 之前处理的文档 available 是 false。
    """
    try:
        return service.page_structure(document_id, page, run)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/pages/{page}/ocr", summary="某一页的 OCR 结果")
def page_ocr(document_id: uuid.UUID, page: Annotated[int, Path(ge=1)], service: Service) -> PageOcr:
    """这一页 OCR 认出的每个文字块：文字、在图上的位置、置信度。坐标和页面图片一样。

    审核页面"从原文选取"用：点哪一块，就把它的编号（index）交给"审核字段"。
    """
    try:
        return service.page_ocr(document_id, page)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/pages/{page}/ocr-region", summary="重新识别一块区域")
def ocr_region(
    document_id: uuid.UUID,
    page: Annotated[int, Path(ge=1)],
    region: RegionIn,
    service: Service,
) -> RegionOut:
    """把这一页上的一块区域（box，像素坐标）裁下来、放大，重新做一次 OCR，返回认出的文字。

    审核时"框选 → 重新识别这块"用：原来 OCR 认错或漏认的地方，单独放大再认一次常常就对了。
    只返回文字，不保存；确认后再用"审核字段"保存（带上 source_box）。
    """
    try:
        return service.ocr_region(document_id, page, region.box)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/export.xlsx", summary="导出 Excel", response_class=Response)
def export_document(document_id: uuid.UUID, service: Service) -> Response:
    """把一个文档导出成 Excel。格子里是审核之后的值，改过的标蓝，批注里有原值和原因。"""
    try:
        filename, data = service.export([document_id])
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, XLSX)


@router.post("/{document_id}/review", summary="完成审核")
def review_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """所有标黄的字段都修改或确认过之后，把文档标记为已审核（reviewed）。

    还有字段没处理时返回 409，并说明还剩几个。
    """
    try:
        return service.review(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/skip", summary="先跳过")
def skip_document(document_id: uuid.UUID, data: SkipIn, service: Service) -> DocumentOut:
    """审核时拿不准的先跳过：不算进"下一份"，列表里用 `?skipped=true` 筛出来，过后再回来。"""
    try:
        return service.skip(document_id, data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/unskip", summary="取消跳过")
def unskip_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """回到待审核里。完成审核、重新处理时也会自动取消跳过。"""
    try:
        return service.unskip(document_id)
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


@router.post(
    "/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, summary="换模板重新处理"
)
def reprocess_document(document_id: uuid.UUID, data: ReprocessIn, service: Service) -> DocumentOut:
    """用选的模板（不选就是现在这个模板的最新版）跑新的一轮。旧结果留着，可以查看。"""
    try:
        return service.reprocess(document_id, data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/adopt-previous", summary="采纳上次的确认")
def adopt_previous(document_id: uuid.UUID, service: Service) -> AdoptOut:
    """重新处理之后：上一轮人审核过、这次值和位置都对得上的字段，一次沿用。"""
    try:
        return service.adopt_previous(document_id)
    except DocumentError as err:
        raise _http_error(err) from err
