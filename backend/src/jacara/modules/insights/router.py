"""Statistics and data export API: /api/v1/insights."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from jacara.core.deps import get_owner, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.owner import Owner
from jacara.core.storage import ObjectStorage
from jacara.modules.documents.router import attachment
from jacara.modules.insights.schemas import AttributionIn, AttributionOut, CorrectionStats
from jacara.modules.insights.service import InsightsError, InsightsService

router = APIRouter(prefix="/insights", tags=["统计"])


def get_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_storage)],
    owner: Annotated[Owner, Depends(get_owner)],
) -> InsightsService:
    return InsightsService(session, owner, storage)


Service = Annotated[InsightsService, Depends(get_service)]


@router.get("/corrections", summary="人工修改统计")
def corrections(
    service: Service,
    template: Annotated[
        str | None, Query(description="只看这个模板（模板名或我的模板的 id）")
    ] = None,
    days: Annotated[int | None, Query(ge=1, le=3650, description="只看最近几天审核完成的")] = None,
) -> CorrectionStats:
    """For reviewed documents: how often each field was corrected, the error kinds, and how many
    wrong values were not flagged."""
    return service.corrections(template, days)


@router.patch("/attributions/{attribution_id}", summary="改判错在哪一步")
def override_attribution(
    attribution_id: uuid.UUID, data: AttributionIn, service: Service
) -> AttributionOut:
    """Override an incorrect automatic attribution. stage null reverts the override."""
    try:
        return service.override(attribution_id, data)
    except InsightsError as err:
        status = {"not_found": 404, "invalid": 422}.get(err.code, 400)
        raise HTTPException(status_code=status, detail=render(err.message)) from err


@router.get("/dataset", summary="导出审核数据", response_class=Response)
def dataset(
    service: Service,
    template: Annotated[str | None, Query()] = None,
    sensitive: Annotated[bool, Query(description="连敏感字段一起导出")] = False,
    files: Annotated[bool, Query(description="带上原文件")] = True,
) -> Response:
    """Package reviewed documents into a zip: original files, training data (train.jsonl) and a
    paired evaluation manifest (pairs.toml)."""
    filename, data = service.dataset(template, sensitive, files)
    return attachment(filename, data, "application/zip")
