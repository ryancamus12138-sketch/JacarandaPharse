"""模板接口：/api/v1/templates（ADR 0030）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import XLSX, attachment
from jacara.modules.templates.schemas import (
    ArchiveIn,
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
    """试跑用的流水线。第一次试跑时才连模型服务；测试里换成假的。"""
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
    return HTTPException(_STATUS.get(err.code, 400), err.message)


@router.get("", summary="所有模板")
def list_templates(
    service: Service,
    builtin: Annotated[bool, Query(description="也列出内置模板（评测和命令行用）")] = False,
) -> list[TemplateOut]:
    """我的模板（kind=mine，没归档的在前）。`?builtin=true` 再加上内置模板（kind=builtin）。"""
    return service.list(builtin)


@router.get("/starters", summary="起步模板库")
def list_starters(service: Service) -> list[TemplateOut]:
    """九个起步模板。"添加到我的模板"之后才能用来上传。"""
    return service.starters()


@router.get("/rules", summary="可以选的规则")
def list_rules(service: Service) -> list[RuleInfo]:
    return service.rules()


@router.post("/formula-check", summary="检查公式或规则条件")
def formula_check(data: FormulaCheckIn, service: Service) -> FormulaCheckOut:
    """编辑器边写边检查：对不对、换成中文名是什么样子。不保存任何东西。"""
    return service.formula_check(data)


@router.post("", status_code=status.HTTP_201_CREATED, summary="新建模板")
def create_template(data: TemplateCreate, service: Service) -> TemplateOut:
    """空白模板、从起步模板复制、或者复制我的另一个模板。"""
    try:
        return service.create(data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/from-sample", status_code=status.HTTP_201_CREATED, summary="用样例建模板")
def create_from_sample(data: SampleIn, service: Service) -> SampleOut:
    """样例文档里选好的键值对 → 新模板；样例文档换成按新模板的结果。"""
    try:
        template, document_id = service.from_sample(data)
    except TemplateFailure as err:
        raise _error(err) from err
    return SampleOut(template=template, document_id=document_id)


@router.post("/import", status_code=status.HTTP_201_CREATED, summary="导入模板")
def import_template(data: TemplateFile, service: Service) -> TemplateOut:
    """导入"导出模板"得到的 JSON 文件，新建一个我的模板（第 1 版）。"""
    try:
        return service.import_file(data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/{name}/export", summary="导出模板", response_class=Response)
def export_template(
    name: str, service: Service, format: Literal["xlsx", "json"] = "xlsx"
) -> Response:
    """xlsx：标准模板 Excel，人能看懂、能改，改完再导入；
    json：全部内容（连按原表导出用的原 Excel），在别的电脑上原样导入。"""
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
    """标准模板 Excel：读出模板（kind=standard），确认后用"导入模板"建；
    业务报表：返回每张 sheet 的角色、每一列的猜测（kind=report），在预览里确认后建。"""
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
    """归档的、用过的都能删。用过的不真的删：从列表、上传里去掉，那些文档照常显示。"""
    try:
        service.delete(template_id)
    except TemplateFailure as err:
        raise _error(err) from err


@router.get("/{name}", summary="一个模板")
def get_template(name: str, service: Service) -> TemplateOut:
    """name：我的模板的 id，或者起步、内置模板的名字。我的模板带上历史版本。"""
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
    """每次保存是一个新版本。已经处理过的文档还是按原来的版本显示和导出。"""
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
    """只改功能领域、国家和地区、自定义标签，不出新版本（ADR 0037）。"""
    try:
        return service.labels(template_id, data)
    except TemplateFailure as err:
        raise _error(err) from err


@router.post("/{template_id}/trial", summary="试跑")
def trial_template(template_id: uuid.UUID, data: TrialIn, service: Service) -> TrialOut:
    """用最新版在一份文档上试抽一次，不保存。要等模型跑完（每页十几秒）。"""
    try:
        return service.trial(template_id, data.document_id)
    except TemplateFailure as err:
        raise _error(err) from err
