"""模板接口收发的 JSON。"""

from __future__ import annotations

from pydantic import BaseModel


class TemplateFieldOut(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    description: str


class TemplateRuleOut(BaseModel):
    id: str
    title: str
    needs: list[str]
    severity: str


class TemplateTableOut(BaseModel):
    name: str
    label: str
    description: str
    columns: list[TemplateFieldOut]


class TemplateOut(BaseModel):
    name: str
    version: int
    title: str
    description: str
    fields: list[TemplateFieldOut]
    rules: list[TemplateRuleOut]
    tables: list[TemplateTableOut] = []
    """表格（例如商品明细）。每一格存成 items.2.amount 这样名字的字段。"""
