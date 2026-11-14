"""Model and dataset library API: /api/v1/hub."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from jacara.core.config import get_settings
from jacara.core.deps import get_owner, get_queue, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.queue import TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import attachment
from jacara.modules.hub.datasets import MAX_BYTES
from jacara.modules.hub.registry import HubError
from jacara.modules.hub.schemas import (
    CardIn,
    DatasetKind,
    DatasetOut,
    EvalIn,
    EvalOut,
    FinishIn,
    HubModels,
    UploadedFile,
    UploadOut,
    VersionOut,
)
from jacara.modules.hub.service import HubService

router = APIRouter(prefix="/hub", tags=["模型和数据集库"])

_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "invalid": status.HTTP_400_BAD_REQUEST,
    "storage": status.HTTP_503_SERVICE_UNAVAILABLE,
    "queue": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service(
    session: Annotated[Session, Depends(get_session)],
    owner: Annotated[Owner, Depends(get_owner)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    queue: Annotated[TaskQueue, Depends(get_queue)],
) -> HubService:
    return HubService(session, owner, get_settings(), storage, queue)


Service = Annotated[HubService, Depends(get_service)]


def _http_error(err: HubError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(err.code, 400), detail=render(err.message))


def _download(filename: str, data: bytes, media_type: str) -> Response:
    return attachment(filename, data, media_type)


# ---- Model library ----


@router.get("/models", summary="模型库")
def models(service: Service) -> HubModels:
    """Models in the library and each version: model card, files, SHA-256, whether in use."""
    return service.models()


@router.post("/models/uploads", summary="开始上传模型文件", status_code=201)
def start_upload(service: Service) -> UploadOut:
    try:
        return service.start_upload()
    except HubError as err:
        raise _http_error(err) from err


@router.put("/models/uploads/{upload_id}/files/{filename}", summary="传一个模型文件")
async def upload_file(
    upload_id: str, filename: str, request: Request, service: Service
) -> UploadedFile:
    """The request body is the file itself (Content-Type: application/octet-stream); multi-gigabyte
    models are supported."""
    length = request.headers.get("content-length")
    try:
        return await service.receive_file(
            upload_id, filename, request.stream(), int(length) if length else None
        )
    except HubError as err:
        raise _http_error(err) from err


@router.post("/models/uploads/{upload_id}/finish", summary="建新版本", status_code=201)
def finish_upload(upload_id: str, data: FinishIn, service: Service) -> VersionOut:
    """Validate the uploaded files (GGUF header; ONNX loads with correct shapes; recognition
    dictionary matches the class count); on success they become the next version of the model."""
    try:
        return service.finish(upload_id, data)
    except HubError as err:
        raise _http_error(err) from err


@router.delete("/models/uploads/{upload_id}", summary="取消上传", status_code=204)
def cancel_upload(upload_id: str, service: Service) -> None:
    try:
        service.cancel_upload(upload_id)
    except HubError as err:
        raise _http_error(err) from err


@router.patch("/models/{name}/versions/{version}", summary="改模型卡")
def update_card(name: str, version: int, data: CardIn, service: Service) -> VersionOut:
    try:
        return service.update_card(name, version, data)
    except HubError as err:
        raise _http_error(err) from err


@router.post("/models/{name}/versions/{version}/use", summary="用这个版本")
def use_version(name: str, version: int, service: Service) -> HubModels:
    """Written to models/selected.json (as with model selection); takes effect after `jacara stop`
    and `jacara start`."""
    try:
        return service.use(name, version)
    except HubError as err:
        raise _http_error(err) from err


@router.delete("/models/{name}/versions/{version}", summary="删掉一个版本", status_code=204)
def delete_version(name: str, version: int, service: Service) -> None:
    try:
        service.delete_version(name, version)
    except HubError as err:
        raise _http_error(err) from err


@router.get("/models/{name}/versions/{version}/download", summary="打包下载一个版本")
def download_version(name: str, version: int, service: Service) -> FileResponse:
    try:
        filename, path = service.pack(name, version)
    except HubError as err:
        raise _http_error(err) from err
    service.session.commit()  # Commit the audit log first: the file is streamed in the response.
    return FileResponse(
        path,
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment(filename, b"", "application/zip").headers[
                "Content-Disposition"
            ]
        },
        background=BackgroundTask(shutil.rmtree, Path(path).parent, ignore_errors=True),
    )


# ---- Dataset library ----


@router.get("/datasets", summary="数据集库")
def datasets(service: Service) -> list[DatasetOut]:
    return service.datasets()


@router.post("/datasets", summary="上传数据集", status_code=201)
def import_dataset(
    service: Service,
    file: Annotated[UploadFile, File(description="PaddleOCR / PaddleX 格式的 zip")],
    name: Annotated[str, Form(min_length=1, max_length=64)],
    kind: Annotated[DatasetKind, Form()],
    description: Annotated[str, Form(max_length=5000)] = "",
) -> DatasetOut:
    data = file.file.read(MAX_BYTES + 1)
    try:
        return service.import_dataset(name, kind, description, data)
    except HubError as err:
        raise _http_error(err) from err


@router.get("/datasets/{dataset_id}/download", summary="下载数据集")
def download_dataset(dataset_id: uuid.UUID, service: Service) -> Response:
    try:
        filename, data = service.dataset_file(dataset_id)
    except HubError as err:
        raise _http_error(err) from err
    return _download(filename, data, "application/zip")


@router.delete("/datasets/{dataset_id}", summary="删除数据集", status_code=204)
def delete_dataset(dataset_id: uuid.UUID, service: Service) -> None:
    try:
        service.delete_dataset(dataset_id)
    except HubError as err:
        raise _http_error(err) from err


# ---- Evaluation ----


@router.get("/evals", summary="OCR 模型评测记录")
def evals(service: Service) -> list[EvalOut]:
    return service.evals()


@router.post("/evals", summary="开始一次 OCR 模型评测", status_code=201)
def create_eval(data: EvalIn, service: Service) -> EvalOut:
    """Queue a background comparison of two models on one split of a dataset."""
    try:
        return service.create_eval(data)
    except HubError as err:
        raise _http_error(err) from err


@router.get("/evals/{run_id}", summary="一次评测的结果")
def eval_run(run_id: uuid.UUID, service: Service) -> EvalOut:
    try:
        return service.eval_run(run_id)
    except HubError as err:
        raise _http_error(err) from err


@router.get("/evals/{run_id}/lines", summary="下载逐条结果")
def eval_lines(run_id: uuid.UUID, service: Service) -> Response:
    try:
        filename, data = service.eval_lines(run_id)
    except HubError as err:
        raise _http_error(err) from err
    return _download(filename, data, "application/x-ndjson")
