"""OCR 样本接口：/api/v1/ocr-samples（ADR 0050）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import attachment
from jacara.modules.hub.schemas import DatasetIn, DatasetOut
from jacara.modules.ocr_samples.det import DetError, DetPageService
from jacara.modules.ocr_samples.schemas import (
    BackfillOut,
    Category,
    DetExportPreview,
    DetPageIn,
    DetPageList,
    DetPageOut,
    DetStatus,
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
    VerifyIn,
    VerifyOut,
)
from jacara.modules.ocr_samples.service import OcrSampleService, SampleError

router = APIRouter(prefix="/ocr-samples", tags=["OCR 样本"])

_STATUS = {
    "conflict": status.HTTP_409_CONFLICT,
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


def get_det_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> DetPageService:
    return DetPageService(session, owner, storage)


DetService = Annotated[DetPageService, Depends(get_det_service)]


def _http_error(err: SampleError | DetError) -> HTTPException:
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


@router.post("/export/dataset", summary="导出并存进数据集库", status_code=201)
def export_to_dataset(
    data: DatasetIn,
    service: Service,
    category: Categories = None,
    source_format: Format = None,
    template: TemplateName = None,
    kind: Kind = "train_val",
) -> DatasetOut:
    """和导出 ZIP 一样（照常记导出记录），存进数据集库（ADR 0059），给 OCR 模型评测用。"""
    try:
        return service.export_to_dataset(
            data.name, data.description, category, source_format, template, kind
        )
    except SampleError as err:
        raise _http_error(err) from err


# ---- 检测样本（ADR 0061） ----


@router.get("/det-pages", summary="能做检测样本的页")
def det_pages(
    service: DetService,
    status_: Annotated[DetStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DetPageList:
    """审核完成的单据里 OCR 认的页（有敏感字段的不算），和每页做到哪了。"""
    return service.candidates(status_, offset, limit)


@router.get("/det-pages/export/preview", summary="检测样本导出预览")
def det_export_preview(service: DetService, kind: Kind = "train_val") -> DetExportPreview:
    return service.preview(kind)


@router.get("/det-pages/export", summary="导出检测样本 ZIP", response_class=Response)
def det_export(service: DetService, kind: Kind = "train_val") -> Response:
    """images/、det_gt_train.txt、det_gt_val.txt（或 det_gt_test.txt）、
    manifest.jsonl、README.md。"""
    try:
        filename, data, _ = service.export(kind)
    except DetError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "application/zip")


@router.post("/det-pages/export/dataset", summary="导出检测样本并存进数据集库", status_code=201)
def det_export_to_dataset(
    data: DatasetIn, service: DetService, kind: Kind = "train_val"
) -> DatasetOut:
    from jacara.core.config import get_settings
    from jacara.core.permissions import require
    from jacara.modules.hub.registry import HubError
    from jacara.modules.hub.service import HubService

    try:
        require(service.owner, "value:review")
        _, zipped, export_id = service.export(kind)
        hub = HubService(service.session, service.owner, get_settings(), service.storage)
        dataset = hub.save_dataset(
            data.name, "ocr_det", "det_pages", data.description, zipped, export_id
        )
    except DetError as err:
        raise _http_error(err) from err
    except HubError as err:
        raise HTTPException(status_code=_STATUS.get(err.code, 400), detail=err.message) from err
    return hub.dataset_out(dataset)


@router.get("/det-pages/{document_id}/{page}", summary="打开一页检测样本")
def det_page(document_id: uuid.UUID, page: int, service: DetService) -> DetPageOut:
    """存过的照存的；没存过的给预标注（OCR 的框 + 审核时改对的原文 + 框选的漏认）。"""
    try:
        return service.get(document_id, page)
    except DetError as err:
        raise _http_error(err) from err


@router.put("/det-pages/{document_id}/{page}", summary="保存这一页的框")
def save_det_page(
    document_id: uuid.UUID, page: int, data: DetPageIn, service: DetService
) -> DetPageOut:
    try:
        return service.save(document_id, page, data)
    except DetError as err:
        raise _http_error(err) from err


@router.post("/det-pages/{document_id}/{page}/{action}", summary="确认、不用、重新打开这一页")
def set_det_page(
    document_id: uuid.UUID,
    page: int,
    action: Literal["confirm", "exclude", "reopen"],
    data: DetPageIn,
    service: DetService,
) -> DetPageOut:
    """confirm：确认（PPOCRLabel 的确认，只有确认过的导出）；exclude：不用这一页；
    reopen：改回没确认。都会同时保存带来的框。"""
    target = {"confirm": "confirmed", "exclude": "excluded", "reopen": "draft"}[action]
    try:
        return service.save(document_id, page, data, target)
    except DetError as err:
        raise _http_error(err) from err


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


@router.post("/confirmed-lines", summary="收认对的行")
def confirmed_lines(service: Service) -> BackfillOut:
    """审核完成的单据里人没改过、出处正好一整块、这一块只有这个值的：OCR 读对了，
    裁成小图进"待复核"（ADR 0055）。同一个字段只收一次。"""
    try:
        return service.collect_confirmed()
    except SampleError as err:
        raise _http_error(err) from err


@router.post("/verify", summary="一次复核通过几条")
def verify_samples(data: VerifyIn, service: Service) -> VerifyOut:
    """样本页"通过这一页"：人看过这一页的小图，待复核的一起变成可以导出。"""
    return service.verify_many(data.ids)


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
