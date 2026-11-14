"""OCR samples API: /api/v1/ocr-samples."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.i18n import render
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
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=render(err.message))


@router.get("/settings", summary="自动收集开没开")
def get_settings(service: Service) -> SampleSettings:
    return service.settings()


@router.put("/settings", summary="打开或关掉自动收集")
def put_settings(change: SampleSettingsIn, service: Service) -> SampleSettings:
    """Administrators only. When enabled, transcriptions entered during review are saved as OCR
    samples."""
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
    """Number of entries to export, groups and lines in training and validation, whether validation
    is sufficient, and excluded entries with reasons."""
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
    """Same as the ZIP export (an export record is written as usual), but stored in the dataset
    library for OCR model evaluation."""
    try:
        return service.export_to_dataset(
            data.name, data.description, category, source_format, template, kind
        )
    except SampleError as err:
        raise _http_error(err) from err


# ---- Detection samples ----


@router.get("/det-pages", summary="能做检测样本的页")
def det_pages(
    service: DetService,
    status_: Annotated[DetStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DetPageList:
    """OCR-read pages of reviewed documents (excluding pages with sensitive fields) and the progress
    of each page."""
    return service.candidates(status_, offset, limit)


@router.get("/det-pages/export/preview", summary="检测样本导出预览")
def det_export_preview(service: DetService, kind: Kind = "train_val") -> DetExportPreview:
    return service.preview(kind)


@router.get("/det-pages/export", summary="导出检测样本 ZIP", response_class=Response)
def det_export(service: DetService, kind: Kind = "train_val") -> Response:
    """images/, det_gt_train.txt, det_gt_val.txt (or det_gt_test.txt), manifest.jsonl, README.md."""
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
        raise HTTPException(
            status_code=_STATUS.get(err.code, 400), detail=render(err.message)
        ) from err
    return hub.dataset_out(dataset)


@router.get("/det-pages/{document_id}/{page}", summary="打开一页检测样本")
def det_page(document_id: uuid.UUID, page: int, service: DetService) -> DetPageOut:
    """Saved boxes if any; otherwise pre-annotation (OCR boxes + transcriptions corrected during
    review + missed-text regions)."""
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
    """confirm: confirm the page (PPOCRLabel confirmation; only confirmed pages are exported);
    exclude: do not use this page; reopen: back to unconfirmed. The submitted boxes are saved in
    all cases."""
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
    """crop_img/, rec_gt_train.txt, rec_gt_val.txt, rec_gt.txt (or rec_gt_test.txt), manifest.jsonl,
    README.md. Every export writes an export record."""
    try:
        filename, data = service.export(category, source_format, template, kind)
    except SampleError as err:
        raise _http_error(err) from err
    return attachment(filename, data, "application/zip")


@router.get("/exports", summary="导出记录")
def export_records(service: Service) -> list[ExportRecordOut]:
    """Groups and lines of each export; problems found later (groups quarantined by merging or
    splitting) are listed in affected."""
    return service.export_records()


@router.get("/groups", summary="单据分组")
def list_groups(service: Service) -> GroupList:
    """Business documents, sample counts and split of each group."""
    return service.group_list()


@router.put("/groups/{group_id}/split", summary="改一组的划分")
def change_split(group_id: uuid.UUID, change: SplitIn, service: Service) -> GroupOut:
    """Administrators: lock test groups and handle quarantined groups. Groups exported into training
    can no longer become validation or test."""
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
    """PDFs and photos whose document type, number, date, amount, currency and issuer match,
    awaiting side-by-side confirmation."""
    return service.pairs()


@router.post("/pairs", summary="确认是不是同一张单据")
def decide_pair(change: PairIn, service: Service) -> PairResult:
    """same: merge into one group (already exported groups in different splits are quarantined as a
    whole); different: recorded and no longer proposed."""
    try:
        return service.decide_pair(change)
    except SampleError as err:
        raise _http_error(err) from err


@router.get("/units", summary="按文件名找单据")
def find_units(
    service: Service, q: Annotated[str, Query(min_length=1, max_length=200)]
) -> list[UnitOut]:
    """For manual pairing: processed documents whose file name contains q."""
    return service.units(q)


@router.post("/backfill", summary="从以前的修改补收样本")
def backfill(service: Service) -> BackfillOut:
    """Crop earlier corrections made by clicking source text or selecting a region into pending
    samples. They can be exported only after a person confirms the transcription. Each correction
    is collected once; repeated calls create no duplicates."""
    try:
        return service.backfill()
    except SampleError as err:
        raise _http_error(err) from err


@router.post("/confirmed-lines", summary="收认对的行")
def confirmed_lines(service: Service) -> BackfillOut:
    """From reviewed documents, collect fields that were not corrected and whose evidence is exactly
    one whole block containing only this value: OCR read them correctly. Cropped into pending
    samples; each field is collected once."""
    try:
        return service.collect_confirmed()
    except SampleError as err:
        raise _http_error(err) from err


@router.post("/verify", summary="一次复核通过几条")
def verify_samples(data: VerifyIn, service: Service) -> VerifyOut:
    """ "Approve page" on the samples page: the reviewer has checked the crops on this page, and the
    pending ones become exportable."""
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
    """Change the transcription or category, withdraw (status=withdrawn) or restore
    (status=ready)."""
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
