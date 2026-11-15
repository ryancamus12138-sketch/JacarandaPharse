"""Templates API: /api/v1/templates."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import XLSX, attachment
from jacara.modules.templates.schemas import (
    ArchiveIn,
    BoxesIn,
    DomainOut,
    ExcelAnalysisOut,
    ExcelBuildIn,
    FormulaCheckIn,
    FormulaCheckOut,
    LabelsIn,
    RuleInfo,
    SampleIn,
    SampleOut,
    TemplateCreate,
    TemplateFile,
    TemplateIn,
    TemplateOut,
    TrialIn,
    TrialOut,
)
from jacara.modules.templates.service import PipelineFactory, TemplateFailure, TemplateService

router = APIRouter(prefix="/templates", tags=["模板"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "conflict": status.HTTP_409_CONFLICT,
    "unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_trial_pipeline() -> PipelineFactory:
    """Pipeline for trial runs. Connects to the model server on the first trial run; replaced by a
    fake in tests."""
    from jacara.worker import get_pipeline

    return get_pipeline


def get_service(
    owner: Annotated[Owner, Depends(get_owner)],
    session: Annotated[Session, Depends(get_session)],
    pipeline: Annotated[PipelineFactory, Depends(get_trial_pipeline)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
) -> TemplateService:
    return TemplateService(owner, session, pipeline, storage)


Service = Annotated[TemplateService, Depends(get_service)]


def _error(err: TemplateFailure) -> HTTPException:
    return HTTPException(_STATUS.get(err.code, 400), render(err.message))


@router.get("", summary="所有模板")
def list_templates(
    service: Service,
    builtin: Annotated[bool, Query(description="也列出内置模板（评测和命令行用）")] = False,
) -> list[TemplateOut]:
    """My templates (kind=mine, non-archived first). `?builtin=true` adds the built-in templates
    (kind=builtin)."""
    return service.list(builtin)


@router.get("/starters", summary="起步模板库")
def list_starters(service: Service) -> list[TemplateOut]:
    """The nine starter templates. Usable for uploads after "Add to my templates"."""
    return service.starters()


@router.get("/domains", summary="常用功能领域")
def list_domains(owner: Annotated[Owner, Depends(get_owner)]) -> list[DomainOut]:
    """Known domains for the tag editor: the stored form and the name in the interface language.
    Templates store a known domain under one name and show it in the language of whoever looks."""
    from jacara.core.i18n import language
    from jacara.templates import DOMAIN_NAMES, domain_label

    require(owner, "document:read")
    return [DomainOut(value=d, label=domain_label(d, language())) for d in DOMAIN_NAMES]


@router.get("/rules", summary="可以选的规则")
def list_rules(service: Service) -> list[RuleInfo]:
    return service.rules()


@router.post("/formula-check", summary="检查公式或规则条件")
def formula_check(data: FormulaCheckIn, service: Service) -> FormulaCheckOut:
    """Live validation in the editor: whether it is valid and how it reads with display names. Saves
    nothing."""
    return service.formula_check(data)


@router.post("", status_code=status.HTTP_201_CREATED, summary="新建模板")
def create_template(data: TemplateCreate, service: Service) -> TemplateOut:
    """A blank template, a copy of a starter template, or a copy of another of my templates."""
    try:
        return service.create(data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/from-sample", status_code=status.HTTP_201_CREATED, summary="用样例建模板")
def create_from_sample(data: SampleIn, service: Service) -> SampleOut:
    """Key-value pairs selected in a sample document → new template; the sample document is
    reprocessed with it."""
    try:
        template, document_id = service.from_sample(data)
    except TemplateFailure as err:
        raise _error(err) from err
    return SampleOut(template=template, document_id=document_id)


@router.post("/from-boxes", status_code=status.HTTP_201_CREATED, summary="在样例上框选建模板")
def create_from_boxes(data: BoxesIn, service: Service) -> TemplateOut:
    """Fields selected as regions on a sample → new template."""
    try:
        return service.from_boxes(data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/import", status_code=status.HTTP_201_CREATED, summary="导入模板")
def import_template(data: TemplateFile, service: Service) -> TemplateOut:
    """Create one of my templates (version 1) from a JSON file produced by "Export template"."""
    try:
        return service.import_file(data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/{name}/export", summary="导出模板", response_class=Response)
def export_template(
    name: str, service: Service, format: Literal["xlsx", "json"] = "xlsx"
) -> Response:
    """xlsx: the standard template workbook, readable and editable, for re-import after editing;
    json: the complete content (including the original workbook for original-sheet export), for
    importing unchanged on another machine."""
    try:
        if format == "xlsx":
            filename, body = service.export_excel(name)
            return attachment(filename, body, XLSX)
        data = service.export_file(name)
    except TemplateFailure as err:
        raise _error(err) from err
    body = data.model_dump_json(indent=2).encode("utf-8")
    return attachment(f"{data.title}.json", body, "application/json")


@router.post("/excel", summary="上传 Excel 建模板")
def analyze_excel(file: UploadFile, service: Service) -> ExcelAnalysisOut:
    """Standard template workbook: returns the template (kind=standard), created after confirmation
    through "Import template". Business report: returns the role of each sheet and a guess for
    each column (kind=report), created after confirmation in the preview."""
    try:
        return service.analyze_excel(file.filename or "upload.xlsx", file.file.read())
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/excel/{upload_id}", summary="再看一次上传的 Excel")
def reanalyze_excel(upload_id: uuid.UUID, service: Service) -> ExcelAnalysisOut:
    try:
        return service.reanalyze_excel(upload_id)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post(
    "/excel/{upload_id}/build", status_code=status.HTTP_201_CREATED, summary="按预览建模板"
)
def build_from_excel(upload_id: uuid.UUID, data: ExcelBuildIn, service: Service) -> TemplateOut:
    try:
        return service.build_excel(upload_id, data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除模板")
def delete_template(template_id: uuid.UUID, service: Service) -> None:
    """Archived and used templates can be deleted. Used templates are not physically deleted: they
    are removed from the list and upload, and their documents display as usual."""
    try:
        service.delete(template_id)
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/{name}", summary="一个模板")
def get_template(name: str, service: Service) -> TemplateOut:
    """name: id of one of my templates, or the name of a starter or built-in template. My templates
    include their version history."""
    try:
        return service.get(name)
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/{template_id}/versions/{version}", summary="模板的某个版本")
def get_version(template_id: uuid.UUID, version: int, service: Service) -> TemplateOut:
    try:
        return service.version(template_id, version)
    except TemplateFailure as err:
        raise _error(err) from err


@router.put("/{template_id}", summary="保存模板（新版本）")
def save_template(template_id: uuid.UUID, data: TemplateIn, service: Service) -> TemplateOut:
    """Every save creates a new version. Documents already processed keep displaying and exporting
    with their original version."""
    try:
        return service.save(template_id, data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/{template_id}/archive", summary="归档或恢复")
def archive_template(template_id: uuid.UUID, data: ArchiveIn, service: Service) -> TemplateOut:
    try:
        return service.archive(template_id, data.archived)
    except TemplateFailure as err:
        raise _error(err) from err


@router.patch("/{template_id}/labels", summary="改标签")
def set_labels(template_id: uuid.UUID, data: LabelsIn, service: Service) -> TemplateOut:
    """Change only domains, country or region, and custom tags; no new version is created."""
    try:
        return service.labels(template_id, data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/{template_id}/trial", summary="试跑")
def trial_template(template_id: uuid.UUID, data: TrialIn, service: Service) -> TrialOut:
    """Trial-extract one document with the latest version without saving. Waits for the model (about
    ten seconds per page)."""
    try:
        return service.trial(template_id, data.document_id)
    except TemplateFailure as err:
        raise _error(err) from err
