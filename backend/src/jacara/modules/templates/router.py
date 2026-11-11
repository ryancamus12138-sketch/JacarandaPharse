"""模板接口：/api/v1/templates。登录了就能看。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from jacara.core.deps import get_owner
from jacara.core.owner import Owner
from jacara.modules.templates.schemas import TemplateOut
from jacara.modules.templates.service import TemplateNotFound, TemplateService

router = APIRouter(prefix="/templates", tags=["模板"])


@router.get("", summary="所有模板")
def list_templates(owner: Annotated[Owner, Depends(get_owner)]) -> list[TemplateOut]:
    """每个模板的字段（名字、中文名、类型、是否必填、给模型的说明）和规则。"""
    return TemplateService(owner).list()


@router.get("/{name}", summary="一个模板")
def get_template(name: str, owner: Annotated[Owner, Depends(get_owner)]) -> TemplateOut:
    try:
        return TemplateService(owner).get(name)
    except TemplateNotFound as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err)) from err
