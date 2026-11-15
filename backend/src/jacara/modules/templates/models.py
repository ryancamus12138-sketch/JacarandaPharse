"""My templates.

templates           A template: name, type, current version, archived flag
template_versions   Every version of a template: the definition (JSON with the same structure as
.toml) never changes, and documents record the version they used, so later template changes do
not alter the fields, rules or export columns of older documents."""

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
    """Type label: invoice, transport, receipt, lodging, other, …"""
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    domains: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """Domains: invoice, transport, receipt, lodging, … (several allowed). category is the first one
    (kept for compatibility)."""
    countries: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """Country or region code (CN, AU, …); empty means generic."""
    tags: Mapped[list[str]] = mapped_column(JsonColumn, default=list, server_default="[]")
    """Custom tags (2026-10 trip, finance department, …). Tags are not part of the template
    definition; changing them does not create a version."""
    latest_version: Mapped[int] = mapped_column(default=1, server_default="1")
    archived: Mapped[bool] = mapped_column(default=False, server_default="false")
    """Archived templates are not offered for upload; documents that used them display as usual."""
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Deletion time. A template that has been used is not physically deleted (its documents still
    need it for display names, rules and export); it is only removed from the template list,
    upload and reprocessing. Unused templates are deleted outright."""
    source: Mapped[str] = mapped_column(String(64), default="blank", server_default="blank")
    """Origin: blank, starter:<starter template name>, copy:<template id>, sample:<document id>."""
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
    """{"fields": [...], "tables": [...], "rules": [...]}; format in templates/__init__.py."""
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    """What changed in this version (an optional one-line note entered when saving)."""

    template: Mapped[TemplateRecord] = relationship(back_populates="versions", lazy="joined")
