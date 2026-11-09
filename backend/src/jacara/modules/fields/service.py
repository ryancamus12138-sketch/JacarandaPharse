"""人工审核的业务逻辑：修改字段、确认字段、查看修改历史。

原值永远不改（项目规则 2）：每次审核都新增一条 value_revisions，当前值取最新一条。
"""

from __future__ import annotations

import math
import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.models import ExtractedValue, ValueRevision
from jacara.modules.audit.service import record
from jacara.modules.fields.repository import ValueRepository
from jacara.modules.fields.schemas import RevisionIn, RevisionOut, ValueHistory, ValueOut

REVIEWABLE = {"done", "reviewed"}
"""这些状态的文档才能审核：处理完了、有结果。"""


class FieldError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FieldService:
    def __init__(self, session: Session, owner: Owner) -> None:
        self.session = session
        self.repo = ValueRepository(session)
        self.owner = owner

    def history(self, value_id: uuid.UUID) -> ValueHistory:
        value = self._find(value_id)
        require(self.owner, "value:read", value.team_id)
        return ValueHistory(
            value=ValueOut.model_validate(value),
            revisions=[RevisionOut.model_validate(r) for r in value.revisions],
        )

    def revise(self, value_id: uuid.UUID, change: RevisionIn) -> ValueOut:
        value = self._find(value_id)
        require(self.owner, "value:review", value.team_id)
        document = value.extraction.document
        if document.status not in REVIEWABLE:
            raise FieldError("not_ready", f"文档状态是 {document.status}，处理完成后才能审核")

        if change.action == "edit":
            new_value = normalize(value.type, change.value)
            reason = change.reason.strip()
            if not reason:
                raise FieldError("invalid", "修改时请写明原因，以后查记录时能看懂")
            if new_value == value.current_value:
                raise FieldError("invalid", "新值和当前值一样。如果值是对的，请用 confirm（确认）")
        else:
            new_value, reason = value.current_value, change.reason.strip()

        revision = ValueRevision(
            original=value,
            seq=len(value.revisions) + 1,
            action=change.action,
            new_value=new_value,
            reason=reason,
            user_id=self.owner.user_id,
            team_id=value.team_id,
        )
        try:
            self.repo.add_revision(revision)
        except IntegrityError as err:
            # 两个人同时审核同一个字段：seq 一样，数据库只让一个写进去
            raise FieldError("conflict", "这个字段刚被别人审核过，请刷新后再试") from err

        record(
            self.session,
            self.owner,
            f"value:{change.action}",
            "value",
            value.id,
            document_id=str(document.id),
            field=value.name,
            value=new_value,
        )
        if document.status == "reviewed":
            document.status = "done"  # 审核完的文档又改了，要重新点"完成审核"
        return ValueOut.model_validate(value)

    def _find(self, value_id: uuid.UUID) -> ExtractedValue:
        value = self.repo.get(value_id, self.owner.team_id)
        if value is None:
            raise FieldError("not_found", "找不到这个字段")
        return value


def normalize(type_: str, raw: str | None) -> str | None:
    """按字段类型检查并整理新值，存成和模型原值一样的格式（数字 "9.0"、日期 "2018-12-25"）。"""
    if raw is None or not raw.strip():
        return None  # 原文里确实没有这个值
    text = raw.strip()
    if type_ == "number":
        try:
            number = float(text.replace(",", ""))  # 允许 1,234.50 这种写法
        except ValueError:
            raise FieldError("invalid", f"{text!r} 不是数字") from None
        if not math.isfinite(number):
            raise FieldError("invalid", f"{text!r} 不是有效的数字")
        return str(number)
    if type_ == "date":
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise FieldError(
                "invalid", f"{text!r} 不是日期，请写成 2018-12-25 这样的格式"
            ) from None
    return text
