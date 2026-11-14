"""Documents API: /api/v1/documents.

The router only parses requests, calls the service and converts results or errors into HTTP
responses. It never touches the database."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_model, get_ocr, get_owner, get_queue, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.repository import DocumentFilter
from jacara.modules.documents.schemas import (
    Dashboard,
    DeleteIn,
    DeleteOut,
    DocumentDetail,
    DocumentNeighbors,
    DocumentOut,
    DocumentPage,
    FolderOut,
    LookIn,
    LookOut,
    PageOcr,
    PageStructureOut,
    RegionDescription,
    RegionIn,
    RegionOut,
    ReprocessIn,
    ReprocessManyIn,
    ReprocessManyOut,
    ReviewTimeIn,
    SentText,
    SkipIn,
    StopManyIn,
    StopManyOut,
)
from jacara.modules.documents.service import DocumentError, DocumentService, day_start
from jacara.modules.fields.schemas import AdoptOut
from jacara.plugins.model_backends import ModelBackend
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
    model: Annotated[ModelBackend, Depends(get_model)],
) -> DocumentService:
    return DocumentService(session, storage, queue, owner, ocr, model)


Service = Annotated[DocumentService, Depends(get_service)]


def _http_error(err: DocumentError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=render(err.message))


def attachment(filename: str, data: bytes, media_type: str) -> Response:
    """Make the browser download the response as a file named filename.

        File names may contain non-ASCII characters: filename* is UTF-8 encoded as specified by
    RFC 6266; filename is an ASCII fallback for clients that do not understand filename*."""
    fallback = f"export{PurePath(filename).suffix}"  # ASCII fallback name: export.xlsx, export.zip
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
    """Upload an image or a PDF. The file is stored in object storage, the document metadata in the
    database, and the document is queued for processing.

        template: id of one of my templates, or discover (no template; extract all key-value
    pairs). Defaults to the template used last. Returns immediately with status uploaded. Poll
    the document details; once the status is done, extraction results are available.

        Batch import: folder is the source folder of the file (lists can filter by it); split is
    the number of documents in the file (auto detects, one for the whole file, pages for one per
    page, or "1-2, 3, 4-6"); with skip_duplicate=true, a file uploaded before is not created
    again and the earlier document is returned (duplicate_skipped=true)."""
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


def list_filters(
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
) -> DocumentFilter:
    """Document list filters from the query string, shared by the list and by previous / next on
    the review page so both walk the same list.

        Filters:
    `?status=done&status=failed&template=receipt&date_from=2026-11-01&q=costco&pending=true`.
        Search field values: `?value_q=044031900111&field=invoice_no` (without field, all fields
    are searched).
        Dates use the time zone of this machine."""
    return DocumentFilter(
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


ListFilters = Annotated[DocumentFilter, Depends(list_filters)]


@router.get("", summary="文档列表")
def list_documents(
    service: Service,
    filters: ListFilters,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    """Ordered by upload time descending. limit is the page size, offset the number of rows to skip
    (pagination). Filters: see list_filters."""
    return service.page(limit, offset, filters)


@router.delete("/{document_id}", summary="删除一份文档")
def delete_document(document_id: uuid.UUID, service: Service) -> DeleteOut:
    """Delete the original file, results, review records and comments permanently. A queued or
    running document is deleted too; its processing stops and writes nothing."""
    try:
        return service.delete([document_id])
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/delete", summary="一次删除几份文档")
def delete_documents(data: DeleteIn, service: Service) -> DeleteOut:
    """Delete the selected documents, queued and running ones included (their processing stops
    and writes nothing)."""
    try:
        return service.delete(data.ids)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/stop", summary="一次停止几份")
def stop_documents(data: StopManyIn, service: Service) -> StopManyOut:
    """Stop the selected queued or running documents; they become failed and can be reprocessed.
    Documents with nothing in progress are listed in skipped."""
    try:
        return service.stop_many(data.ids)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/stop-all", summary="全部停止")
def stop_all_documents(service: Service) -> StopManyOut:
    """Stop every queued and running document; they become failed and can be reprocessed."""
    try:
        return service.stop_all()
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/reprocess", status_code=status.HTTP_202_ACCEPTED, summary="一次重新处理几份")
def reprocess_documents(data: ReprocessManyIn, service: Service) -> ReprocessManyOut:
    """Reprocess the selected documents with the same settings (template, document split, vision,
    PDF read mode, reason). Omitted settings: each document's latest template version, the
    previous split, and system defaults for vision and PDF read mode. Documents being processed
    or still queued are restarted."""
    try:
        return service.reprocess_many(data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/folders", summary="批量导入的文件夹")
def list_folders(service: Service) -> list[FolderOut]:
    """Batch-import folders and the number of documents in each (for filtering the list by
    folder)."""
    return service.folders()


@dashboard_router.get("", summary="工作台")
def dashboard(service: Service) -> Dashboard:
    """Dashboard statistics and to-dos: counts per status, processed and reviewed today, processing
    queue (with progress), documents needing attention first."""
    return service.dashboard()


@router.get("/export.xlsx", summary="导出多个文档", response_class=Response)
def export_documents(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
    lang: Annotated[
        str | None,
        Query(max_length=16, description="列名用哪种语言：zh、en、ja……；不写用模板的主语言"),
    ] = None,
) -> Response:
    """Export several documents to one Excel workbook: a summary sheet with one row per document,
    for reconciliation.

        Usage: `/api/v1/documents/export.xlsx?ids=FIRST_ID&ids=SECOND_ID`"""
    try:
        filename, data = service.export(ids, lang)
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, XLSX)


@router.get("/export.csv", summary="导出 CSV", response_class=Response)
def export_documents_csv(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
    lang: Annotated[
        str | None,
        Query(max_length=16, description="列名用哪种语言：zh、en、ja……；不写用模板的主语言"),
    ] = None,
) -> Response:
    """Export CSV (UTF-8) with the same content as the Excel summary: one row per document.

        A single .csv when there is only the summary table; with line-item tables or key-value
    pairs, one .csv per table packed into a zip.
        Usage: `/api/v1/documents/export.csv?ids=FIRST_ID&ids=SECOND_ID`"""
    try:
        filename, data, kind = service.export_csv(ids, lang)
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "text/csv; charset=utf-8" if kind == "csv" else ZIP)


@router.get("/export.json", summary="导出 JSON", response_class=Response)
def export_documents_json(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
    lang: Annotated[
        str | None,
        Query(max_length=16, description="字段名（labels）用哪种语言；不写用模板的主语言"),
    ] = None,
) -> Response:
    """Export JSON (UTF-8) for other programs: document → each sub-document → fields, tables, labels
    and needs_review. Values are post-review, as in Excel.

        Usage: `/api/v1/documents/export.json?ids=FIRST_ID&ids=SECOND_ID`"""
    try:
        filename, data = service.export_json(ids, lang)
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "application/json")


@router.get("/export.zip", summary="导出多个文档（每个一个 Excel）", response_class=Response)
def export_documents_zip(
    service: Service,
    ids: Annotated[list[uuid.UUID], Query(description="要导出的文档 id，可以写多个")],
    lang: Annotated[
        str | None,
        Query(max_length=16, description="列名用哪种语言：zh、en、ja……；不写用模板的主语言"),
    ] = None,
) -> Response:
    """One Excel workbook per document, packed into a zip that extracts to a folder.

    Usage: `/api/v1/documents/export.zip?ids=FIRST_ID&ids=SECOND_ID`"""
    try:
        filename, data = service.export_zip(ids, lang)
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, ZIP)


@router.get("/{document_id}", summary="文档详情")
def get_document(
    document_id: uuid.UUID,
    service: Service,
    run: Annotated[int | None, Query(ge=1)] = None,
) -> DocumentDetail:
    """Document metadata with the extracted fields, evidence and rule check results of each page.

    run: which run to show (earlier runs are read-only). Defaults to the current run."""
    try:
        return service.get(document_id, run)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/neighbors", summary="上一份、下一份")
def document_neighbors(
    document_id: uuid.UUID, service: Service, filters: ListFilters
) -> DocumentNeighbors:
    """Paging on the review page: the previous and next documents of the list (same filters as the
    list, upload time descending, any status) and this document's position in it; plus the next
    document awaiting review, opened after a review is completed."""
    try:
        return service.neighbors(document_id, filters)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/file", summary="下载原始文件", response_class=Response)
def download_document(document_id: uuid.UUID, service: Service) -> Response:
    """The original uploaded file. The review page displays it."""
    try:
        document, data = service.download(document_id)
    except DocumentError as err:
        raise _http_error(err) from err
    return Response(content=data, media_type=document.content_type or "application/octet-stream")


@router.get("/{document_id}/pages/{page}/image", summary="某一页的图片", response_class=Response)
def page_image(
    document_id: uuid.UUID, page: Annotated[int, Path(ge=1)], service: Service
) -> Response:
    """Page page (1-based) rendered as PNG. The review page displays it and draws evidence boxes
    from each field's evidence.bbox.

        PDFs are rendered at the same scale as during extraction and images at their original
    pixel size, so coordinates match."""
    try:
        data = service.page_image(document_id, page)
    except DocumentError as err:
        raise _http_error(err) from err
    # The page image never changes, so browsers may cache it for an hour; private: cached only in
    # the user's browser.
    return Response(
        content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get(
    "/{document_id}/pages/{page}/crop", summary="某一页上一块区域的放大图", response_class=Response
)
def page_crop(
    document_id: uuid.UUID,
    page: Annotated[int, Path(ge=1)],
    service: Service,
    box: Annotated[str, Query(pattern=r"^-?[\d.]+(,-?[\d.]+){3}$")],
) -> Response:
    """Zoomed PNG of box="x0,y0,x1,y1" (page image coordinates). Shown next to candidate values on
    the review page to reveal the exact characters."""
    try:
        data = service.page_crop(document_id, page, [float(v) for v in box.split(",")])
    except DocumentError as err:
        raise _http_error(err) from err
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
    """Layout regions, table cells (row and column) and label → value pairs detected on this page.

        Drawn over the page image in the layout view of the review page. available is false for
    documents processed before 2026-11-14."""
    try:
        return service.page_structure(document_id, page, run)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/extractions/{extraction_id}/sent", summary="发给模型的文字")
def sent_text(document_id: uuid.UUID, extraction_id: uuid.UUID, service: Service) -> SentText:
    """Document text and text block indices sent to the model for this document in this run. To
    diagnose a missed value: if the value is absent here, OCR did not recognize it; if present,
    extraction missed it."""
    try:
        return service.sent_text(document_id, extraction_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/pages/{page}/ocr", summary="某一页的 OCR 结果")
def page_ocr(document_id: uuid.UUID, page: Annotated[int, Path(ge=1)], service: Service) -> PageOcr:
    """Every text block recognized on this page: text, position on the image and confidence, in page
    image coordinates.

        Used by "select from source text" on the review page: the index of the clicked block is
    passed to field review."""
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
    """Crop a region of this page (box, pixel coordinates), enlarge it, run OCR again and return the
    recognized text.

        Used by "select region → re-recognize" during review: text that OCR misread or missed is
    often recognized correctly when enlarged on its own. Returns text only and saves nothing;
    after confirmation, save with field review (including source_box)."""
    try:
        return service.ocr_region(document_id, page, region.box)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/pages/{page}/describe-region", summary="框选建模板：框里是什么")
def describe_region(
    document_id: uuid.UUID,
    page: Annotated[int, Path(ge=1)],
    region: RegionIn,
    service: Service,
) -> RegionDescription:
    """Template building by region selection: the value inside the box, the nearby label, its
    position on the page and its likely type. Read-only."""
    try:
        return service.describe_region(document_id, page, region.box)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/pages/{page}/look-region", summary="让模型看一块区域")
def look_region(
    document_id: uuid.UUID,
    page: Annotated[int, Path(ge=1)],
    data: LookIn,
    service: Service,
) -> LookOut:
    """Crop a region of this page and send it to the vision model to read the text and the value of
    the target field.

        For handwriting, stamps and text covered by pen marks that OCR cannot read. Returns the
    result only and saves nothing; save with field review after confirmation. Returns 503 when
    the model server has no vision projector loaded."""
    try:
        return service.look_region(document_id, page, data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.get("/{document_id}/export.xlsx", summary="导出 Excel", response_class=Response)
def export_document(
    document_id: uuid.UUID,
    service: Service,
    lang: Annotated[
        str | None,
        Query(max_length=16, description="列名用哪种语言：zh、en、ja……；不写用模板的主语言"),
    ] = None,
) -> Response:
    """Export one document to Excel. Cells contain post-review values; edited cells are blue and
    carry the original value and reason in a cell comment."""
    try:
        filename, data = service.export([document_id], lang)
    except DocumentError as err:
        raise _http_error(err) from err
    return attachment(filename, data, XLSX)


@router.post("/{document_id}/review", summary="完成审核")
def review_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """Mark the document as reviewed once every flagged field has been edited or confirmed.

    Returns 409 with the number of remaining fields if any are still open."""
    try:
        return service.review(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post(
    "/{document_id}/review-time", status_code=status.HTTP_204_NO_CONTENT, summary="记审核用时"
)
def add_review_time(document_id: uuid.UUID, data: ReviewTimeIn, service: Service) -> None:
    """Reported by the review page: seconds of active use of the review page in this interval (time
    in a background tab or while idle is excluded)."""
    try:
        service.add_review_time(document_id, data.seconds)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/skip", summary="先跳过")
def skip_document(document_id: uuid.UUID, data: SkipIn, service: Service) -> DocumentOut:
    """Skip a document during review: it is excluded from "Next" and can be listed with
    `?skipped=true` to return to later."""
    try:
        return service.skip(document_id, data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/unskip", summary="取消跳过")
def unskip_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """Return the document to the review queue. Completing review or reprocessing also clears the
    skip."""
    try:
        return service.unskip(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/process", status_code=status.HTTP_202_ACCEPTED, summary="重新处理")
def process_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """Requeue a failed (failed) or still queued (uploaded) document.

    202 means accepted for processing, not completed."""
    try:
        return service.process(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/stop", summary="停止处理")
def stop_document(document_id: uuid.UUID, service: Service) -> DocumentOut:
    """Stop processing a queued or running document; it becomes failed and can be reprocessed.
    Reprocessing a running document (POST /reprocess) restarts it instead."""
    try:
        return service.stop(document_id)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post(
    "/{document_id}/reprocess", status_code=status.HTTP_202_ACCEPTED, summary="换模板重新处理"
)
def reprocess_document(document_id: uuid.UUID, data: ReprocessIn, service: Service) -> DocumentOut:
    """Run a new pass with the selected template (default: the latest version of the current
    template). Earlier results are kept for inspection."""
    try:
        return service.reprocess(document_id, data)
    except DocumentError as err:
        raise _http_error(err) from err


@router.post("/{document_id}/adopt-previous", summary="采纳上次的确认")
def adopt_previous(document_id: uuid.UUID, service: Service) -> AdoptOut:
    """After reprocessing: carry over fields reviewed in the previous run whose value and position
    still match, in one step."""
    try:
        return service.adopt_previous(document_id)
    except DocumentError as err:
        raise _http_error(err) from err
