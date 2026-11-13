"""OCR 样本接口：/api/v1/ocr-samples（ADR 0050）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import attachment
from jacara.modules.ocr_samples.schemas import (
    Category,
    ExportPreview,
    SampleIn,
    SampleList,
    SampleOut,
    SampleSettings,
    SampleSettingsIn,
    SourceFormat,
    Status,
)
from jacara.modules.ocr_samples.service import OcrSampleService, SampleError

router = APIRouter(prefix="/ocr-samples", tags=["OCR 样本"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "storage": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> OcrSampleService:
    return OcrSampleService(session, owner, storage)


Service = Annotated[OcrSampleService, Depends(get_service)]
Categories = Annotated[list[Category] | None, Query(description="只看这几种分类")]
Format = Annotated[SourceFormat | None, Query(description="pdf 或 image")]
TemplateName = Annotated[str | None, Query(max_length=200, description="只看这个模板")]


def _http_error(err: SampleError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message)


@router.get("/settings", summary="自动收集开没开")
def get_settings(service: Service) -> SampleSettings:
    return service.settings()


@router.put("/settings", summary="打开或关掉自动收集")
def put_settings(change: SampleSettingsIn, service: Service) -> SampleSettings:
    """只有管理员能改。打开后，审核时"图中原文"保存成 OCR 样本。"""
    return service.set_settings(change.collect)


@router.get("", summary="样本列表")
def list_samples(
    service: Service,
    category: Categories = None,
    state: Annotated[Status | None, Query(alias="status")] = None,
    source_format: Format = None,
    template: TemplateName = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SampleList:
    return service.list(category, state, source_format, template, limit, offset)


@router.get("/templates", summary="有样本的模板")
def sample_templates(service: Service) -> list[str]:
    return service.templates()


@router.get("/export/preview", summary="导出预览")
def export_preview(
    service: Service,
    category: Categories = None,
    source_format: Format = None,
    template: TemplateName = None,
) -> ExportPreview:
    """会导出几条、不导出的几条和原因（撤回了、裁图失败、同一张图两种写法……）。"""
    return service.preview(category, source_format, template)


@router.get("/export", summary="导出 ZIP", response_class=Response)
def export_samples(
    service: Service,
    category: Categories = None,
    source_format: Format = None,
    template: TemplateName = None,
) -> Response:
    """crop_img/、rec_gt.txt（PaddleOCR 识别训练直接读）、manifest.jsonl、README.md。"""
    try:
        filename, data = service.export(category, source_format, template)
    except SampleError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "application/zip")


@router.get("/{sample_id}/image", summary="样本的小图", response_class=Response)
def sample_image(sample_id: uuid.UUID, service: Service) -> Response:
    try:
        data = service.image(sample_id)
    except SampleError as err:
        raise _http_error(err) from err
    return Response(content=data, media_type="image/png")


@router.patch("/{sample_id}", summary="改一条样本")
def update_sample(sample_id: uuid.UUID, change: SampleIn, service: Service) -> SampleOut:
    """改图中原文、改分类、撤回（status=withdrawn）或恢复（status=ready）。"""
    try:
        return service.update(sample_id, change)
    except SampleError as err:
        raise _http_error(err) from err


@router.post("/{sample_id}/retry", summary="重新裁图")
def retry_sample(sample_id: uuid.UUID, service: Service) -> SampleOut:
    try:
        return service.retry(sample_id)
    except SampleError as err:
        raise _http_error(err) from err
