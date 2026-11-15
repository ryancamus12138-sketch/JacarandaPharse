"""Public schema of audit log entries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict

from jacara.core.i18n import render_stored


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    detail: Annotated[dict[str, Any], AfterValidator(render_stored)]
    """Details of the operation; reasons written by the system are rendered in the reader's
    language."""


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
