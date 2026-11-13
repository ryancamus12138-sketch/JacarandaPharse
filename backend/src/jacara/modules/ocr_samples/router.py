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
    BackfillOut,
    Category,
    ExportKind,
    ExportPreview,
    ExportRecordOut,
    GroupList,
    GroupOut,
    PairIn,
    PairOut,
    PairResult,
    SampleIn,
    SampleList,
    SampleOut,
    SampleSettings,
    SampleSettingsIn,
    SourceFormat,
    SplitIn,
    Status,
    UnitOut,
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
Kind = Annotated[ExportKind, Query(description="train_val：训练和验证；test：锁定测试集")]


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
    kind: Kind = "train_val",
) -> ExportPreview:
    """会导出几条、训练和验证各几组几行、验证集够不够、不导出的几条和原因。"""
    return service.preview(category, source_format, template, kind)


@router.get("/export", summary="导出 ZIP", response_class=Response)
def export_samples(
    service: Service,
    category: Categories = None,
    source_format: Format = None,
    template: TemplateName = None,
    kind: Kind = "train_val",
) -> Response:
    """crop_img/、rec_gt_train.txt、rec_gt_val.txt、rec_gt.txt（或 rec_gt_test.txt）、
    manifest.jsonl、README.md。每次导出记一条导出记录（ADR 0052）。"""
    try:
        filename, data = service.export(category, source_format, template, kind)
    except SampleError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "application/zip")


@router.get("/exports", summary="导出记录")
def export_records(service: Service) -> list[ExportRecordOut]:
    """每次导出了几组几行；导出以后发现的问题（合并、拆开时隔离的组）记在 affected 里。"""
    return service.export_records()


@router.get("/groups", summary="单据分组")
def list_groups(service: Service) -> GroupList:
    """每一组有哪几张业务单据、几条样本、分在哪边（ADR 0052）。"""
    return service.group_list()


@router.put("/groups/{group_id}/split", summary="改一组的划分")
def change_split(group_id: uuid.UUID, change: SplitIn, service: Service) -> GroupOut:
    """管理员：锁定测试集、处理隔离的组。已经导出进训练集的不能再当验证集或测试集。"""
    try:
        return service.change_split(group_id, change.split)
    except SampleError as err:
        raise _http_error(err) from err


@router.post("/groups/members/{member_id}/detach", summary="拆开配错的单据")
def detach_member(member_id: uuid.UUID, service: Service) -> PairResult:
    try:
        return service.detach(member_id)
    except SampleError as err:
        raise _http_error(err) from err


@router.get("/pairs", summary="可能是同一张单据的")
def list_pairs(service: Service) -> list[PairOut]:
    """PDF 和照片：单据类型、单号、日期、金额、币种、发单方对得上的，等人并排看原图确认。"""
    return service.pairs()


@router.post("/pairs", summary="确认是不是同一张单据")
def decide_pair(change: PairIn, service: Service) -> PairResult:
    """same：合并成一组（已经导出过、分在不同边的整组隔离）；different：记下来，不再提名。"""
    try:
        return service.decide_pair(change)
    except SampleError as err:
        raise _http_error(err) from err


@router.get("/units", summary="按文件名找单据")
def find_units(
    service: Service, q: Annotated[str, Query(min_length=1, max_length=200)]
) -> list[UnitOut]:
    """手动配对时用：文件名里有 q 的、处理完的单据。"""
    return service.units(q)


@router.post("/backfill", summary="从以前的修改补收样本")
def backfill(service: Service) -> BackfillOut:
    """以前审核时点了原文或框了一块、改了值的，裁出小图进"待复核"（ADR 0053）。
    人确认图里的原文以后才能导出。同一次修改只收一条，再点不会重复。"""
    try:
        return service.backfill()
    except SampleError as err:
        raise _http_error(err) from err


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
