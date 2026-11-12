"""我的模板（ADR 0030）。

templates           一个模板：名字、类型、现在是第几版、归档没有
template_versions   模板的每一个版本：定义（和 .toml 一样结构的 JSON）永远不改，
                    文档记下用的是哪个版本，以后模板再改，旧文档的字段、规则、导出列都不变
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class TemplateRecord(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(32), default="", server_default="")
    """类型标签：发票、交通、收据、住宿、其他……"""
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    domains: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """功能领域（ADR 0037）：发票、交通、收据、住宿……可以有几个。
    category 是第一个（兼容旧代码）。"""
    countries: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """国家和地区代码（CN、AU……）；空 = 通用。"""
    tags: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """自定义标签（2026-10 出差、财务部……）。标签不是模板定义的一部分，改标签不出新版本。"""
    latest_version: Mapped[int] = mapped_column(default=1, server_default="1")
    archived: Mapped[bool] = mapped_column(default=False, server_default="false")
    """归档的模板不出现在上传的选择里，用过它的文档照常显示。"""
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """删除的时间。用过的模板删除时不真的删（那些文档还要靠它显示中文名、检查规则、导出），
    只是从模板列表、上传、重新处理里都去掉；没用过的直接删掉。"""
    source: Mapped[str] = mapped_column(String(64), default="blank", server_default="blank")
    """怎么来的：blank（空白）、starter:<起步模板名>、copy:<模板 id>、sample:<文档 id>。"""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list[TemplateVersion]] = relationship(
        back_populates="template", order_by="TemplateVersion.version"
    )


class TemplateVersion(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id"), index=True)
    version: Mapped[int]
    definition: Mapped[dict[str, Any]]
    """{"fields": [...], "tables": [...], "rules": [...]}，格式见 templates/__init__.py。"""
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    """这一版改了什么（保存时写的一句话，可以不写）。"""

    template: Mapped[TemplateRecord] = relationship(back_populates="versions", lazy="joined")
