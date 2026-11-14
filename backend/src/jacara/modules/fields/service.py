"""Review business logic: editing and confirming fields, selecting from source text, adding and
removing table rows, viewing history.

Original values never change: each review inserts a value_revisions row and the current value is
the latest one. Deleting a row also inserts revisions (action="delete") instead of deleting;
adding a row inserts fields with origin="manual". Computed fields are recalculated by formula
when inputs change or rows are added or removed, recording action="compute"; computed fields
edited directly by a person no longer follow their inputs."""

from __future__ import annotations

import math
import uuid
from datetime import date
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jacara.core.i18n import stored, tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage
from jacara.core.textvalue import parse_date, parse_number
from jacara.models import ExtractedValue, Extraction, ValueRevision
from jacara.modules.audit.service import record
from jacara.modules.fields.repository import ValueRepository
from jacara.modules.fields.schemas import (
    ConfirmManyOut,
    RevisionIn,
    RevisionOut,
    RowChange,
    RowIn,
    ValueHistory,
    ValueOut,
)
from jacara.pipeline.store import evidence_from_ocr
from jacara.plugins.extractors.base import cell_name, split_cell_name

REVIEWABLE = {"done", "reviewed"}
"""Statuses in which a document can be reviewed: processed, with results."""


class FieldError(Exception):
    """Business error. code tells the router which HTTP status to return."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FieldService:
    def __init__(
        self, session: Session, owner: Owner, storage: ObjectStorage | None = None
    ) -> None:
        self.session = session
        self.repo = ValueRepository(session)
        self.owner = owner
        self.storage = storage
        """Stores crops when collecting OCR samples. No collection without it."""

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
            raise FieldError(
                "not_ready", tr("文档状态是 {status}，处理完成后才能审核", status=document.status)
            )
        _check_current(value.extraction)
        if value.deleted:
            raise FieldError("invalid", tr("这一行已经删除了，要改的话先恢复这一行"))

        evidence: dict[str, Any] | None = None
        if change.source_lines is not None and change.source_box is not None:
            raise FieldError("invalid", tr("从原文选取和框选只能用一种"))
        if change.transcription is not None and (
            change.action != "edit"
            or (change.source_box is None and len(change.source_lines or []) != 1)
        ):
            raise FieldError("invalid", tr("图中原文要对应原图上的一块原文或一个框"))
        sample_line: dict[str, Any] | None = None
        page = self._page(value.extraction, change.page)
        if change.action == "edit" and change.source_box is not None:
            # Region re-recognition: the value was checked and confirmed by a person; the evidence
            # is the selected region.
            new_value = normalize(value.type, change.value)
            if new_value is None:
                raise FieldError("invalid", tr("框里没有要填的值，请框住要识别的文字"))
            x0, y0, x1, y1 = change.source_box
            evidence = {
                "page": page,
                "row": None,
                "row_text": new_value,
                "text": new_value,
                "bbox": [
                    round(min(x0, x1), 1),
                    round(min(y0, y1), 1),
                    round(max(x0, x1), 1),
                    round(max(y0, y1), 1),
                ],
                "confidence": 1.0,  # Confirmed by a person.
                "similarity": 1.0,
                "drawn": True,
            }
            reason = change.reason.strip() or stored("框选重新识别")
        elif change.action == "edit" and change.source_lines is not None:
            # Select from source text: the value is parsed from the selected text and the evidence
            # becomes the selected blocks.
            lines = self._ocr_lines(value.extraction, change.source_lines, page)
            evidence = {"page": page, **evidence_from_ocr(lines)}
            if change.transcription is not None:
                sample_line = lines[0]
            if change.value is None:
                text = change.transcription or " ".join(ln["text"] for ln in lines)
                new_value = value_from_text(value.type, text)
            else:
                new_value = normalize(value.type, change.value)
            reason = change.reason.strip() or stored("从原文选取")
        elif change.action == "edit":
            new_value = normalize(value.type, change.value)
            reason = change.reason.strip()
            if not reason:
                raise FieldError("invalid", tr("修改时请写明原因，以后查记录时能看懂"))
            if new_value == value.current_value:
                raise FieldError(
                    "invalid", tr("新值和当前值一样。如果值是对的，请用 confirm（确认）")
                )
        else:
            new_value, reason = value.current_value, change.reason.strip()

        revision = self._add(value, change.action, new_value, reason, evidence)
        sample = None
        if change.transcription is not None:
            from jacara.modules.ocr_samples.service import collect

            sample = collect(
                self.session,
                self.storage,
                self.owner,
                value,
                revision.id,
                page,
                change.transcription,
                line=sample_line,
                box=change.source_box,
            )
        record(
            self.session,
            self.owner,
            f"value:{change.action}",
            "value",
            value.id,
            document_id=str(document.id),
            field=value.name,
            value=new_value,
            picked=evidence is not None,
            **({"ocr_sample": str(sample.id)} if sample is not None else {}),
        )
        self._touch(value.extraction)
        return ValueOut.model_validate(value)

    def confirm_many(self, ids: list[uuid.UUID], reason: str) -> ConfirmManyOut:
        """Confirm several fields at once: one confirm revision per field and one audit log entry
        (with all ids).

                Deleted rows are skipped; each field is confirmed only once."""
        values = [self._find(i) for i in dict.fromkeys(ids)]
        for value in values:
            require(self.owner, "value:review", value.team_id)
            if value.extraction.document.status not in REVIEWABLE:
                status = value.extraction.document.status
                raise FieldError(
                    "not_ready", tr("文档状态是 {status}，处理完成后才能审核", status=status)
                )
            _check_current(value.extraction)
        reason = reason.strip() or stored("批量确认")
        done = [v for v in values if not v.deleted]
        for value in done:
            self._add(value, "confirm", value.current_value, reason, None)
            self._touch(value.extraction)
        if done:
            record(
                self.session,
                self.owner,
                "value:confirm_many",
                "extraction",
                done[0].extraction_id,
                document_id=str(done[0].extraction.document_id),
                count=len(done),
                value_ids=[str(v.id) for v in done],
                reason=reason,
            )
        return ConfirmManyOut(confirmed=len(done), skipped=len(values) - len(done))

    # ---- Table rows ----

    def add_row(self, extraction_id: uuid.UUID, table: str, row: RowIn) -> list[ValueOut]:
        """Add a table row: one empty field per column (origin="manual"), filled later by editing or
        "select from source text"."""
        extraction = self._extraction(extraction_id)
        require(self.owner, "value:review", extraction.team_id)
        self._check_reviewable(extraction)
        columns = _columns(extraction, table)
        computed = _computed_columns(extraction, table)
        rows = _rows(extraction, table)
        if row.after not in (None, 0) and row.after not in rows:
            raise FieldError(
                "not_found", tr("表格 {table} 里没有第 {after} 行", table=table, after=row.after)
            )

        # Number of the new row: larger than all existing rows, so it never collides.
        index = max(rows, default=0) + 1
        start, end = _gap(extraction, table, rows, row.after)
        position = max((v.position for v in extraction.values), default=-1) + 1
        created: list[ExtractedValue] = []
        for k, (column, type_) in enumerate(columns):
            value = ExtractedValue(
                extraction=extraction,
                position=position + k,
                sort_key=start + (end - start) * (k + 1) / (len(columns) + 1),
                origin="computed" if column in computed else "manual",
                name=cell_name(table, index, column),
                type=type_,
                value=None,
                row_no=None,
                status="empty",
                needs_review=False,
                note=stored("审核时人工增加的行") if column not in computed else stored("按公式算"),
                evidence=None,
                user_id=self.owner.user_id,
                team_id=extraction.team_id,
            )
            self.session.add(value)
            created.append(value)
        self.session.flush()
        record(
            self.session,
            self.owner,
            "value:add_row",
            "extraction",
            extraction.id,
            document_id=str(extraction.document_id),
            table=table,
            row=index,
            after=row.after,
            reason=row.reason.strip(),
        )
        self._touch(extraction)
        for value in created:
            self.session.refresh(value, ["revisions"])
        return [ValueOut.model_validate(v) for v in created]

    def delete_row(
        self, extraction_id: uuid.UUID, table: str, index: int, change: RowChange
    ) -> list[ValueOut]:
        """Delete a row: a delete revision is added for each cell. Nothing is deleted, so the row
        can be restored."""
        return self._row_action(extraction_id, table, index, change, "delete")

    def restore_row(
        self, extraction_id: uuid.UUID, table: str, index: int, change: RowChange
    ) -> list[ValueOut]:
        """Restore a deleted row: a restore revision is added for each cell, with the value before
        deletion."""
        return self._row_action(extraction_id, table, index, change, "restore")

    def _row_action(
        self, extraction_id: uuid.UUID, table: str, index: int, change: RowChange, action: str
    ) -> list[ValueOut]:
        extraction = self._extraction(extraction_id)
        require(self.owner, "value:review", extraction.team_id)
        self._check_reviewable(extraction)
        cells = _rows(extraction, table).get(index)
        if not cells:
            raise FieldError(
                "not_found", tr("表格 {table} 里没有第 {index} 行", table=table, index=index)
            )
        reason = change.reason.strip()
        if not reason:
            raise FieldError(
                "invalid",
                tr("删除一行时请写明原因，以后查记录时能看懂")
                if action == "delete"
                else tr("恢复一行时请写明原因，以后查记录时能看懂"),
            )
        deleted = all(v.deleted for v in cells)
        if action == "delete" and deleted:
            raise FieldError("invalid", tr("这一行已经删除了"))
        if action == "restore" and not deleted:
            raise FieldError("invalid", tr("这一行没有被删除"))
        for value in cells:
            self._add(value, action, value.current_value, reason, None)
        record(
            self.session,
            self.owner,
            f"value:{action}_row",
            "extraction",
            extraction.id,
            document_id=str(extraction.document_id),
            table=table,
            row=index,
            reason=reason,
        )
        self._touch(extraction)
        return [ValueOut.model_validate(v) for v in cells]

    # ---- Shared ----

    def _add(
        self,
        value: ExtractedValue,
        action: str,
        new_value: str | None,
        reason: str,
        evidence: dict[str, Any] | None,
    ) -> ValueRevision:
        revision = ValueRevision(
            original=value,
            seq=len(value.revisions) + 1,
            action=action,
            new_value=new_value,
            reason=reason,
            evidence=evidence,
            user_id=self.owner.user_id,
            team_id=value.team_id,
        )
        try:
            self.repo.add_revision(revision)
        except IntegrityError as err:
            # Two people reviewing the same field concurrently produce the same seq; the database
            # accepts only one.
            raise FieldError("conflict", tr("这个字段刚被别人审核过，请刷新后再试")) from err
        return revision

    def apply(self, value: ExtractedValue, action: str, new_value: str | None, reason: str) -> None:
        """Record one revision when carrying over a previous review (the caller writes a single
        audit log entry)."""
        _check_current(value.extraction)
        self._add(value, action, new_value, reason, None)

    def _touch(self, extraction: Extraction) -> None:
        self._recompute(extraction)
        document = extraction.document
        if document.status == "reviewed":
            # A reviewed document was changed again; review must be completed again.
            document.status = "done"

    def _recompute(self, extraction: Extraction) -> None:
        """Recalculate computed fields from the current values and record a compute revision when a
        value changes.

                Computed fields edited directly by a person (latest revision is edit) no longer
        change: the person's value prevails."""
        from jacara.pipeline.compute import compute
        from jacara.pipeline.current import template_of, typed_value

        template = template_of(extraction)
        if template is None or not template.formulas():
            return
        alive = [v for v in extraction.values if not v.deleted]
        now = {v.name: typed_value(v.type, v.current_value) for v in alive}
        by_name = {v.name: v for v in alive}
        changed: list[str] = []
        for result in compute(template, now):
            value = by_name.get(result.name)
            new_value = None if result.value is None else str(result.value)
            if value is None or new_value == value.current_value:
                continue
            if value.revisions and value.revisions[-1].action == "edit":
                continue
            self._add(value, "compute", new_value, result.note, None)
            changed.append(value.name)
        if changed:
            record(
                self.session,
                self.owner,
                "value:compute",
                "extraction",
                extraction.id,
                document_id=str(extraction.document_id),
                fields=changed,
            )

    def _check_reviewable(self, extraction: Extraction) -> None:
        status = extraction.document.status
        if status not in REVIEWABLE:
            raise FieldError(
                "not_ready", tr("文档状态是 {status}，处理完成后才能审核", status=status)
            )
        _check_current(extraction)

    def _extraction(self, extraction_id: uuid.UUID) -> Extraction:
        extraction = self.repo.get_extraction(extraction_id, self.owner.team_id)
        if extraction is None:
            raise FieldError("not_found", tr("找不到这一页的抽取结果"))
        return extraction

    def _page(self, extraction: Extraction, page: int | None) -> int:
        """The selected page must belong to this document."""
        if page is None:
            return extraction.page
        if not extraction.page <= page <= (extraction.last_page or extraction.page):
            raise FieldError("invalid", tr("第 {page} 页不属于这张单据", page=page))
        return page

    def _ocr_lines(
        self, extraction: Extraction, indexes: list[int], page: int | None = None
    ) -> list[dict[str, Any]]:
        from jacara.pipeline.pages import ocr_page_json

        ocr = ocr_page_json(extraction.ocr, page or extraction.page, extraction.page)
        lines: list[dict[str, Any]] = (ocr or {}).get("lines", [])
        if not lines:
            raise FieldError(
                "invalid", tr("这一页没有保存 OCR 结果（新功能上线前处理的），不能从原文选取")
            )
        if not indexes:
            raise FieldError("invalid", tr("请至少选一块文字"))
        if any(not 0 <= i < len(lines) for i in indexes):
            raise FieldError("invalid", tr("选的文字块不存在，请刷新后再试"))
        return [lines[i] for i in dict.fromkeys(indexes)]

    def _find(self, value_id: uuid.UUID) -> ExtractedValue:
        value = self.repo.get(value_id, self.owner.team_id)
        if value is None:
            raise FieldError("not_found", tr("找不到这个字段"))
        return value


def _check_current(extraction: Extraction) -> None:
    """Earlier runs are read-only: review applies only to the current run."""
    current = extraction.document.current_run
    if extraction.run != current:
        raise FieldError(
            "invalid",
            tr(
                "这是第 {run} 轮的旧结果，只能查看；现在用的是第 {current} 轮",
                run=extraction.run,
                current=current,
            ),
        )


def normalize(type_: str, raw: str | None) -> str | None:
    """Validate and normalize a new value by field type, stored in the same format as model values
    (numbers "9.0", dates "2018-12-25")."""
    if raw is None or not raw.strip():
        return None  # The value is indeed absent from the source.
    text = raw.strip()
    if type_ == "number":
        try:
            number = float(text.replace(",", ""))  # Accept forms such as 1,234.50.
        except ValueError:
            raise FieldError("invalid", tr("{text} 不是数字", text=repr(text))) from None
        if not math.isfinite(number):
            raise FieldError("invalid", tr("{text} 不是有效的数字", text=repr(text)))
        return str(number)
    if type_ == "date":
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            # Source spellings are accepted too (August 3 , 2014, 25/12/2018) and stored as
            # YYYY-MM-DD.
            day = parse_date(text)
            if day is None:
                raise FieldError(
                    "invalid",
                    tr("{text} 读不出日期，请写成 2018-12-25 这样的格式", text=repr(text)),
                ) from None
            return day.isoformat()
    return text


def value_from_text(type_: str, text: str) -> str | None:
    """Parse a value from the selected source text, stored in the same format as model values."""
    if type_ == "number":
        number = parse_number(text)
        if number is None:
            raise FieldError("invalid", tr("选的文字「{text}」里没有数字", text=text))
        return str(number)
    if type_ == "date":
        day = parse_date(text)
        if day is None:
            raise FieldError("invalid", tr("选的文字「{text}」读不出日期，请手动填写", text=text))
        return day.isoformat()
    return " ".join(text.split()) or None


def _rows(extraction: Extraction, table: str) -> dict[int, list[ExtractedValue]]:
    """Every table row (by row number): all cells of that row."""
    rows: dict[int, list[ExtractedValue]] = {}
    for value in extraction.values:
        cell = split_cell_name(value.name)
        if cell and cell[0] == table:
            rows.setdefault(cell[1], []).append(value)
    return rows


def _computed_columns(extraction: Extraction, table: str) -> set[str]:
    """Formula columns of this table."""
    from jacara.pipeline.current import template_of

    template = template_of(extraction)
    return set(template.formulas().columns.get(table, {})) if template else set()


def _columns(extraction: Extraction, table: str) -> list[tuple[str, str]]:
    """Columns of a new row (name, type): from the template, or from existing rows if the template
    is unavailable."""
    from jacara.pipeline.current import template_of

    template = template_of(extraction)
    if template is not None:
        for t in template.tables:
            if t.name == table:
                return [(c.name, c.type) for c in t.columns]
    columns: dict[str, str] = {}
    for value in extraction.values:
        cell = split_cell_name(value.name)
        if cell and cell[0] == table:
            columns.setdefault(cell[2], value.type)
    if not columns:
        raise FieldError("not_found", tr("这一页没有叫 {table} 的表格", table=table))
    return list(columns.items())


def _gap(
    extraction: Extraction,
    table: str,
    rows: dict[int, list[ExtractedValue]],
    after: int | None,
) -> tuple[float, float]:
    """Bounds for the display order of a new row: the last cell of row after and the field
    immediately following it.

        after=0 inserts at the top of the table, before the first cell of the first row."""
    keys = sorted(v.sort_key for v in extraction.values)
    if after == 0 and rows:
        first = min(v.sort_key for cells in rows.values() for v in cells)
        earlier = [k for k in keys if k < first]
        return (earlier[-1] if earlier else first - 1.0), first
    if after:
        start = max(v.sort_key for v in rows[after])
    elif rows:
        start = max(v.sort_key for cells in rows.values() for v in cells)
    else:
        start = keys[-1] if keys else 0.0
    later = [k for k in keys if k > start]
    return start, (later[0] if later else start + 1.0)
