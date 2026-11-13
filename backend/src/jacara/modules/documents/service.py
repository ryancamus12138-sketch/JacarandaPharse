"""文档的业务逻辑：上传、列表、详情、下载、重新处理、完成审核、导出。

权限检查、状态变化只写在这里（项目分层规则）。service 不知道 HTTP，
抛出的是业务错误（DocumentError），由 router 翻译成 HTTP 状态码。
"""

from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import tempfile
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, cast

from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.daterange import parse_date_range
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.queue import QueueError, TaskQueue
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateVersion
from jacara.modules.audit.service import record
from jacara.modules.comments.repository import CommentRepository
from jacara.modules.documents.repository import DocumentFilter, DocumentRepository
from jacara.modules.documents.schemas import (
    REPROCESS_REASONS,
    Dashboard,
    DashboardCounts,
    DeleteOut,
    DocumentDetail,
    DocumentNeighbors,
    DocumentOut,
    DocumentPage,
    ExtractionOut,
    FolderOut,
    LookIn,
    LookOut,
    OcrLineOut,
    PageOcr,
    PageStructureOut,
    RegionDescription,
    RegionOut,
    ReprocessIn,
    RunOut,
    SameFile,
    SkipIn,
    StructureCell,
    StructurePair,
    StructureRegion,
    StructureTable,
)
from jacara.modules.exports.service import save_export
from jacara.modules.fields.schemas import AdoptOut, RangeOut, SuggestionOut
from jacara.modules.fields.service import FieldService
from jacara.modules.templates.service import Pick, TemplateFailure, TemplateService
from jacara.pipeline.current import (
    current_checks_json,
    evidence_text,
    field_states,
    label_for,
    labels_of,
    previous_extraction,
    source_value,
    suggestions,
    template_of,
    wants_source,
)
from jacara.pipeline.entities import entity_conflicts
from jacara.pipeline.load import load_reports
from jacara.pipeline.pages import covering, ocr_page_json, page_slice, pages_json
from jacara.pipeline.region import read_region, region_png
from jacara.pipeline.segment import SplitError, normalize_split
from jacara.plugins.exporters import create_exporter
from jacara.plugins.exporters.csv_files import workbook_to_csv
from jacara.plugins.exporters.excel import cell_value
from jacara.plugins.extractors import ExtractionError
from jacara.plugins.extractors.base import DISCOVER, split_cell_name
from jacara.plugins.extractors.llm import look_at
from jacara.plugins.model_backends import ModelBackend, ModelBackendError
from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.loader import page_png
from jacara.templates import Template
from jacara.workbook.fill import DocRow, fill_workbook

log = logging.getLogger(__name__)

REPROCESSABLE = {"done", "reviewed", "failed"}
"""这些状态的文档可以换模板重新处理（新的一轮）。"""

RETRYABLE = {"uploaded", "failed"}
"""这些状态的文档可以（重新）放进队列。processing 正在处理，done 已经有结果。"""

EXPORTABLE = {"done", "reviewed"}
"""处理完、有结果的文档才能导出。"""

MAX_EXPORT = 100
"""一次最多导出几个文档。"""

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
}


DASHBOARD_LIMIT = 8
"""工作台"需要先处理"每类最多列几份。"""


def start_of_today() -> datetime:
    """今天 0 点（按本机时区的日期，换成 UTC）。工作台的"今天"按用户这台电脑的日期算。"""
    return day_start(datetime.now().astimezone().date())


def day_start(day: date) -> datetime:
    """本机时区某一天的 0 点，换成 UTC（数据库里的时间按 UTC 比较）。"""
    return datetime.combine(day, datetime.min.time()).astimezone().astimezone(UTC)


class DocumentError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentService:
    def __init__(
        self,
        session: Session,
        storage: ObjectStorage,
        queue: TaskQueue,
        owner: Owner,
        ocr: OcrEngine | None = None,
        model: ModelBackend | None = None,
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.storage = storage
        self.queue = queue
        self.ocr = ocr
        """只有"重新识别这块"用到。"""
        self.model = model
        """只有"让模型看这块"用到（ADR 0038）。"""
        self.owner = owner

    def upload(
        self,
        filename: str,
        content_type: str,
        data: bytes,
        template: str | None = None,
        *,
        folder: str | None = None,
        split: str = "auto",
        skip_duplicate: bool = False,
    ) -> DocumentOut:
        """template：我的模板的 id，或者 discover（抽出所有键值对）。不写就用上一次用的。

        folder：批量导入时文件所在的文件夹；split：文件里有几张单据（ADR 0036）；
        skip_duplicate：同一个文件之前传过就不再新建，返回之前那份。
        """
        require(self.owner, "document:create")
        split = check_split(split)
        sha256 = hashlib.sha256(data).hexdigest()
        if skip_duplicate:
            earlier = self.repo.first_with_hash(self.owner.team_id, sha256)
            if earlier is not None:
                out = self._with_counts([earlier])[0]
                return out.model_copy(update={"duplicate_skipped": True})
        try:
            pick = TemplateService(self.owner, self.session).pick_for_upload(template)
        except TemplateFailure as err:
            raise DocumentError(err.code, err.message) from err
        if content_type not in ALLOWED_TYPES:
            # 有的客户端不告诉我们类型（application/octet-stream），按文件后缀猜一次
            content_type = mimetypes.guess_type(filename)[0] or content_type
        if content_type not in ALLOWED_TYPES:
            raise DocumentError("unsupported", f"不支持的文件类型：{content_type}，支持图片和 PDF")
        limit = get_settings().max_upload_mb
        if len(data) > limit * 1024 * 1024:
            raise DocumentError("too_large", f"文件超过 {limit} MB")
        if not data:
            raise DocumentError("empty", "文件是空的")

        document_id = uuid.uuid4()
        safe_name = PurePath(filename).name or f"upload{ALLOWED_TYPES[content_type]}"
        key = f"documents/{document_id}/{safe_name}"
        try:
            self.storage.put(key, data, content_type)  # 先存文件，再写数据库
        except StorageError as err:
            raise DocumentError("storage", "对象存储暂时不可用，请稍后再试") from err

        document = self.repo.add(
            Document(
                id=document_id,
                filename=safe_name,
                content_type=content_type,
                sha256=sha256,
                folder=clean_folder(folder),
                split=split,
                size_bytes=len(data),
                page_count=_count_pages(content_type, data),
                storage_key=key,
                status="uploaded",
                template_version_id=pick.version_id,
                discover=pick.discover,
                auto=pick.auto,
                user_id=self.owner.user_id,
                team_id=self.owner.team_id,
            )
        )
        record(
            self.session,
            self.owner,
            "document:upload",
            "document",
            document.id,
            filename=safe_name,
            template=pick.audit,
            **({"folder": document.folder} if document.folder else {}),
        )
        self._enqueue(document)
        return self._with_counts([document])[0]

    def process(self, document_id: uuid.UUID) -> DocumentOut:
        """重新放进队列：处理失败后重试，或者上传时队列没连上。"""
        document = self._find(document_id)
        require(self.owner, "document:process", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", "这个文档没有保存原始文件（命令行导入的），无法处理")
        if document.status not in RETRYABLE:
            raise DocumentError("conflict", f"文档状态是 {document.status}，不能重新处理")
        document.status = "uploaded"
        document.error = None
        record(self.session, self.owner, "document:process", "document", document.id)
        if not self._enqueue(document):
            raise DocumentError("queue", "任务队列暂时不可用，请稍后再试")
        return DocumentOut.model_validate(document)

    def reprocess(self, document_id: uuid.UUID, data: ReprocessIn) -> DocumentOut:
        """重新处理（ADR 0030）：用选的模板（或现在这个模板的最新版）跑新的一轮。

        旧的几轮都留着，可以查看；新一轮处理完才换过去，失败了旧结果照常能用。
        审核完的文档变回待审核，审计日志记下原因。
        """
        document = self._find(document_id)
        require(self.owner, "document:process", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", "这个文档没有保存原始文件（命令行导入的），无法处理")
        if document.status not in REPROCESSABLE:
            raise DocumentError("conflict", f"文档状态是 {document.status}，现在不能重新处理")
        note = data.note.strip()
        if data.reason == "other" and not note:
            raise DocumentError("invalid", '原因选了"其他"，请写一句说明')
        templates = TemplateService(self.owner, self.session)
        try:
            if data.template is not None:
                pick = templates.pick_for_upload(data.template)
            elif document.auto:
                pick = Pick(None, auto=True)  # 重新自动判断：模板改了、加了新模板都会用上
            elif document.template_version_id is not None:
                # 同一个模板的最新版（"模板改了"最常见）
                current = self.session.get_one(TemplateVersion, document.template_version_id)
                pick = Pick(templates.latest_version_id(current.template_id))
            else:
                pick = Pick(None, discover=document.discover)
        except TemplateFailure as err:
            raise DocumentError(err.code, err.message) from err

        if data.split is not None:
            document.split = check_split(data.split)
        label = REPROCESS_REASONS[data.reason]
        document.template_version_id = pick.version_id
        document.discover = pick.discover
        document.auto = pick.auto
        if data.vision is not None:
            document.vision = None if data.vision == "default" else data.vision
        if data.pdf_read is not None:
            document.pdf_read = None if data.pdf_read == "default" else data.pdf_read
        document.run_reason = f"{label}：{note}" if note else label
        document.status = "uploaded"
        document.error = None
        document.reviewed_at = None
        _clear_skip(document)
        record(
            self.session,
            self.owner,
            "document:reprocess",
            "document",
            document.id,
            reason=data.reason,
            note=note,
            template=pick.audit,
            **({"vision": data.vision} if data.vision is not None else {}),
            **({"pdf_read": data.pdf_read} if data.pdf_read is not None else {}),
        )
        if not self._enqueue(document):
            raise DocumentError("queue", "任务队列暂时不可用，请稍后再试")
        return DocumentOut.model_validate(document)

    def adopt_previous(self, document_id: uuid.UUID) -> AdoptOut:
        """采纳上次的确认：重新处理之后，把能沿用的上一轮审核一次用上（见 suggestions）。"""
        document = self._find(document_id)
        require(self.owner, "value:review", document.team_id)
        if document.status not in ("done", "reviewed"):
            raise DocumentError("conflict", f"文档状态是 {document.status}，处理完成后才能审核")
        fields = FieldService(self.session, self.owner)
        confirmed = edited = 0
        for extraction in document.extractions:
            by_id = {v.id: v for v in extraction.values}
            found = suggestions(extraction, previous_extraction(document, extraction))
            for value_id, (action, value, run) in found.items():
                reason = f"沿用第 {run} 轮的审核"
                fields.apply(by_id[value_id], action, value, reason)
                confirmed += action == "confirm"
                edited += action == "edit"
        if confirmed or edited:
            if document.status == "reviewed":
                document.status = "done"
            record(
                self.session,
                self.owner,
                "document:adopt_previous",
                "document",
                document.id,
                confirmed=confirmed,
                edited=edited,
            )
        return AdoptOut(confirmed=confirmed, edited=edited)

    def delete(self, ids: list[uuid.UUID]) -> DeleteOut:
        """删除文档：原始文件、每一轮的结果、审核记录、备注都删掉，不能恢复。审计日志记下删了什么。

        正在处理的不删（worker 正在用它），等处理完再删。导出记录里的文件不受影响。
        """
        documents = [self._find(i) for i in dict.fromkeys(ids)]
        for document in documents:
            require(self.owner, "document:delete", document.team_id)
        keys: list[str] = []
        skipped: list[str] = []
        deleted = 0
        for document in documents:
            if document.status == "processing":
                skipped.append(f"{document.filename}：正在处理，等处理完再删")
                continue
            record(
                self.session,
                self.owner,
                "document:delete",
                "document",
                document.id,
                filename=document.filename,
                pages=document.page_count,
                status=document.status,
            )
            if document.storage_key:
                keys.append(document.storage_key)
            self.repo.delete(document)
            deleted += 1
        # 先提交，再删原始文件：数据库没删成的话，文件还在
        self.session.commit()
        for key in keys:
            try:
                self.storage.delete(key)
            except StorageError:
                log.warning("删除文档后，原始文件 %s 没能从对象存储删掉", key)
        return DeleteOut(deleted=deleted, skipped=skipped)

    def folders(self) -> list[FolderOut]:
        require(self.owner, "document:read")
        return [FolderOut(folder=f, count=c) for f, c in self.repo.folders(self.owner.team_id)]

    def page(self, limit: int, offset: int, filters: DocumentFilter | None = None) -> DocumentPage:
        require(self.owner, "document:read")
        documents, total = self.repo.page(self.owner.team_id, limit, offset, filters)
        return DocumentPage(items=self._with_counts(documents), total=total)

    def _with_counts(self, documents: list[Document]) -> list[DocumentOut]:
        """文档 + 每份还有几个字段等人确认、几条备注没解决。"""
        ids = [d.id for d in documents]
        counts = self.repo.review_counts(ids)
        comments = CommentRepository(self.session).open_counts(ids)
        same = self.repo.same_files(self.owner.team_id, documents)
        return [
            DocumentOut.model_validate(d).model_copy(
                update={
                    "review_count": counts.get(d.id, 0),
                    "open_comments": comments.get(d.id, 0),
                    "same_file": [
                        SameFile.model_validate(o, from_attributes=True)
                        for o in same.get(d.id, [])[:3]
                    ],
                }
            )
            for d in documents
        ]

    def neighbors(self, document_id: uuid.UUID) -> DocumentNeighbors:
        """审核页面的"上一份 / 下一份"：按列表顺序，前后两份待审核（done）的文档。"""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        previous, following, remaining = self.repo.neighbors(document, ("done",))
        return DocumentNeighbors(previous=previous, next=following, remaining=remaining)

    def dashboard(self) -> Dashboard:
        """工作台：各状态多少份、今天处理和审核了多少、处理队列、需要先处理的。"""
        require(self.owner, "document:read")
        team = self.owner.team_id
        status = self.repo.status_counts(team)
        today = start_of_today()
        average = self.repo.average_page_ms(team, today - timedelta(days=7))
        counts = DashboardCounts(
            queued=status.get("uploaded", 0),
            processing=status.get("processing", 0),
            to_review=status.get("done", 0),
            failed=status.get("failed", 0),
            reviewed=status.get("reviewed", 0),
            total=sum(status.values()),
            skipped=self.repo.skipped_count(team),
            pending_fields=self.repo.pending_fields(team),
            processed_today=self.repo.processed_since(team, today),
            reviewed_today=self.repo.reviewed_since(team, today),
            average_page_seconds=None if average is None else round(average / 1000, 1),
        )
        to_review = [
            DocumentOut.model_validate(d).model_copy(update={"review_count": n})
            for d, n in self.repo.most_pending(team, DASHBOARD_LIMIT)
        ]
        return Dashboard(
            counts=counts,
            queue=[DocumentOut.model_validate(d) for d in self.repo.in_progress(team, 20)],
            failed=[DocumentOut.model_validate(d) for d in self.repo.failed(team, DASHBOARD_LIMIT)],
            to_review=to_review,
        )

    def get(self, document_id: uuid.UUID, run: int | None = None) -> DocumentDetail:
        """文档详情。run：看第几轮的结果（旧的几轮只能看）；不写就是现在用的这一轮。"""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        viewing = run or document.current_run
        if viewing == document.current_run:
            chosen = list(document.extractions)
        else:
            chosen = [e for e in document.all_extractions if e.run == viewing]
            if not chosen:
                raise DocumentError("not_found", f"这个文档没有第 {viewing} 轮的结果")
        detail = DocumentDetail.model_validate(document).model_copy(
            update={"extractions": [ExtractionOut.model_validate(e) for e in chosen]}
        )
        reviews = sum(v.pending_review for e in document.extractions for v in e.values)
        current = viewing == document.current_run
        extractions = [
            _with_states(
                out,
                e,
                previous_extraction(document, e) if current else None,
                self.session if current else None,
            )
            for out, e in zip(detail.extractions, chosen, strict=True)
        ]
        hints = sum(v.suggestion is not None for e in extractions for v in e.values)
        return detail.model_copy(
            update={
                "review_count": reviews,
                "extractions": extractions,
                "runs": _runs(document),
                "viewing_run": viewing,
                "suggestions": hints,
            }
        )

    def page_ocr(self, document_id: uuid.UUID, page: int) -> PageOcr:
        """第 page 页的 OCR 结果：每个文字块的文字和位置。审核页面"从原文选取"用。"""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        extraction = covering(document.extractions, page)
        if extraction is None:
            raise DocumentError("not_found", f"第 {page} 页没有抽取结果")
        ocr = ocr_page_json(extraction.ocr, page, extraction.page)
        lines = (ocr or {}).get("lines", [])
        return PageOcr(
            available=ocr is not None,
            lines=[OcrLineOut(index=i, **line) for i, line in enumerate(lines)],
        )

    def page_structure(
        self, document_id: uuid.UUID, page: int, run: int | None = None
    ) -> PageStructureOut:
        """第 page 页的结构：版面区域、表格的格子、标题 → 值。审核页"版面结构"用。"""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        wanted = run or document.current_run
        extraction = covering((e for e in document.all_extractions if e.run == wanted), page)
        if extraction is None:
            raise DocumentError("not_found", f"第 {page} 页没有抽取结果")
        return structure_view(extraction.structure, page, extraction.page)

    def review(self, document_id: uuid.UUID) -> DocumentOut:
        """完成审核：所有需要确认的字段都处理过了，文档状态 done → reviewed。"""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status != "done":
            raise DocumentError(
                "conflict", f"文档状态是 {document.status}，处理完成（done）后才能完成审核"
            )
        pending = sum(v.pending_review for e in document.extractions for v in e.values)
        if pending:
            raise DocumentError("conflict", f"还有 {pending} 个字段等待确认")
        document.status = "reviewed"
        document.reviewed_at = datetime.now(UTC)
        _clear_skip(document)
        record(self.session, self.owner, "document:review", "document", document.id)
        return DocumentOut.model_validate(document)

    def skip(self, document_id: uuid.UUID, data: SkipIn) -> DocumentOut:
        """先跳过：拿不准的先放一放。跳过的不算进"下一份"，列表里可以筛出来，过后再回来审。"""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.status != "done":
            raise DocumentError("conflict", "只有处理完、还没完成审核的文档可以跳过")
        document.skipped_at = datetime.now(UTC)
        document.skip_reason = data.reason.strip() or None
        document.skipped_by = self.owner.user_id
        record(
            self.session,
            self.owner,
            "document:skip",
            "document",
            document.id,
            reason=document.skip_reason or "",
        )
        return self._with_counts([document])[0]

    def unskip(self, document_id: uuid.UUID) -> DocumentOut:
        """取消跳过：回到待审核里，"下一份"又会轮到它。"""
        document = self._find(document_id)
        require(self.owner, "document:review", document.team_id)
        if document.skipped_at is not None:
            _clear_skip(document)
            record(self.session, self.owner, "document:unskip", "document", document.id)
        return self._with_counts([document])[0]

    def export(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes]:
        """把一个或多个文档导出成一个 Excel（汇总表一行一张单据），返回（文件名，文件内容）。

        格子里是审核之后的值（current_value）；改过的标蓝，批注里写原值和原因。
        """
        documents = self._exportable(document_ids)
        data, layout = self._excel(documents, language)
        if len(documents) == 1:
            name = f"{PurePath(documents[0].filename).stem}.xlsx"
        elif layout:
            # 按原表导出：用原表的名字
            name = f"{PurePath(layout).stem}-{date.today():%Y%m%d}-{len(documents)}份.xlsx"
        else:
            name = f"jacara-{date.today():%Y%m%d}-{len(documents)}份.xlsx"
        self._record_export(documents, "xlsx", name, data)
        return name, data

    def export_zip(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes]:
        """每个文档单独一个 Excel，打包成一个 zip。

        浏览器一次只能下载一个文件，没法直接下载文件夹，所以打包；解压后就是一个文件夹。

        zip 里的文件名跟原文件同名（receipt.jpg → receipt.xlsx），重名的加 (2)、(3)。
        """
        documents = self._exportable(document_ids)
        out = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for document in documents:
                name = _unique(f"{PurePath(document.filename).stem}.xlsx", used)
                archive.writestr(name, self._excel([document], language)[0])
        name = f"jacara-{date.today():%Y%m%d}-{len(documents)}份.zip"
        data = out.getvalue()
        self._record_export(documents, "zip", name, data)
        return name, data

    def export_csv(
        self, document_ids: list[uuid.UUID], language: str | None = None
    ) -> tuple[str, bytes, str]:
        """导出 CSV：内容和标准格式的 Excel 一样（汇总一行一张单据，明细表、键值对各一张）。

        只有汇总一张表时下载一个 .csv；还有明细表、键值对时，每张表一个 .csv 打包成 zip。
        返回（文件名，内容，格式 csv 或 zip）。
        """
        documents = self._exportable(document_ids)
        files = workbook_to_csv(_workbook(documents, language))
        if len(documents) == 1:
            stem = PurePath(documents[0].filename).stem
        else:
            stem = f"jacara-{date.today():%Y%m%d}-{len(documents)}份"
        if len(files) == 1:
            name, data, kind = f"{stem}.csv", files[0][1], "csv"
        else:
            out = io.BytesIO()
            used: set[str] = set()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
                for title, content in files:
                    archive.writestr(_unique(f"{title}.csv", used), content)
            name, data, kind = f"{stem}-csv.zip", out.getvalue(), "zip"
        self._record_export(documents, "csv" if kind == "csv" else "csv-zip", name, data)
        return name, data, kind

    def _excel(self, documents: list[Document], language: str | None = None) -> tuple[bytes, str]:
        """导出成 Excel：模板有原表样式（从 Excel 建的模板）就填回原表，否则用标准格式。

        返回（文件内容，原表的文件名；标准格式时为空）。原表在对象存储里取不到时退回标准格式。
        """
        template = _layout_template(documents)
        if template is not None and template.workbook is not None:
            try:
                original = self.storage.get(template.workbook.file)
            except StorageError:
                log.warning("取不到模板 %s 的原表，改用标准格式导出", template.name)
            else:
                rows = _doc_rows(documents, template)
                title = f"{date.today():%Y-%m-%d} 导出"
                return fill_workbook(template, original, rows, title), template.workbook.filename
        return _workbook(documents, language), ""

    def _exportable(self, document_ids: list[uuid.UUID]) -> list[Document]:
        """导出前的检查（两种导出都一样）：有权限、数量对、都找得到、都处理完了。按用户选的顺序返回。"""
        require(self.owner, "document:export")
        ids = list(dict.fromkeys(document_ids))  # 去掉重复的 id，保持原来的顺序
        if not ids:
            raise DocumentError("invalid", "请至少选择一个文档")
        if len(ids) > MAX_EXPORT:
            raise DocumentError("invalid", f"一次最多导出 {MAX_EXPORT} 个文档")

        found = {d.id: d for d in self.repo.get_many(ids, self.owner.team_id)}
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise DocumentError("not_found", f"找不到文档：{', '.join(missing)}")
        documents = [found[i] for i in ids]
        not_ready = [d.filename for d in documents if d.status not in EXPORTABLE]
        if not_ready:
            raise DocumentError("conflict", f"这些文档还没处理完，不能导出：{', '.join(not_ready)}")
        return documents

    def _record_export(
        self, documents: list[Document], file_format: str, filename: str, data: bytes
    ) -> None:
        # 导出是"数据离开系统"，要留记录：谁、什么时候、导出了哪些文档、什么格式。
        # 文件也存一份，导出记录页可以重新下载（ADR 0029）
        export = save_export(
            self.session, self.storage, self.owner, filename, file_format, data, documents
        )
        record(
            self.session,
            self.owner,
            "document:export",
            "export",
            export.id,
            documents=[str(d.id) for d in documents],
            format=file_format,
        )

    def download(self, document_id: uuid.UUID) -> tuple[Document, bytes]:
        document = self._find(document_id)
        require(self.owner, "document:download", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", "这个文档没有保存原始文件（命令行导入的）")
        try:
            return document, self.storage.get(document.storage_key)
        except StorageError as err:
            raise DocumentError("storage", "对象存储暂时不可用，请稍后再试") from err

    def page_image(self, document_id: uuid.UUID, page: int) -> bytes:
        """第 page 页（从 1 开始）的 PNG 图片。坐标和抽取时一样，审核页面在上面画出处框。"""
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", "这个文档没有保存原始文件（命令行导入的）")
        if not 1 <= page <= document.page_count:
            raise DocumentError(
                "not_found", f"这个文档只有 {document.page_count} 页，没有第 {page} 页"
            )
        try:
            data = self.storage.get(document.storage_key)
        except StorageError as err:
            raise DocumentError("storage", "对象存储暂时不可用，请稍后再试") from err
        try:
            return page_png(data, document.content_type == "application/pdf", page)
        except Exception as err:
            raise DocumentError("unreadable", "这个文件打不开，可能已损坏") from err

    def describe_region(
        self, document_id: uuid.UUID, page: int, box: list[float]
    ) -> RegionDescription:
        """框选建模板（ADR 0047）：框里的值（先用存下的 OCR，没有再局部重新识别）、旁边的标签、
        在页面哪里、像什么类型。"""
        from jacara.modules.templates.boxes import (
            field_id,
            guess_type,
            hint,
            lines_in,
            pair_for,
            position,
        )

        document = self._find(document_id)
        require(self.owner, "template:manage", document.team_id)
        extraction = covering(document.extractions, page)
        ocr = ocr_page_json(extraction.ocr, page, extraction.page) if extraction else None
        lines = (ocr or {}).get("lines", [])
        inside = lines_in(lines, box)
        text = " ".join(str(ln["text"]) for ln in inside)
        confidence = min((float(ln["confidence"]) for ln in inside), default=None)
        if not text and self.ocr is not None:
            try:
                text, confidence = read_region(self.page_image(document_id, page), box, self.ocr)
            except ValueError as err:
                raise DocumentError("invalid", str(err)) from err
        # 标签只用版面分析配好的"标题 → 值"（处理单据时就算好了），对不上就留空，不猜
        structure = (
            structure_view(extraction.structure, page, extraction.page) if extraction else None
        )
        pair = pair_for(structure.pairs, box) if structure else None
        label = pair.key if pair else None
        side = SIDES.get(pair.rule) if pair else None
        where = position(
            box, float((ocr or {}).get("width") or 1), float((ocr or {}).get("height") or 1)
        )
        return RegionDescription(
            text=text,
            confidence=confidence,
            label=label,
            side=side,
            type=guess_type(text),  # type: ignore[arg-type]
            position=where,
            name=field_id(label or "", set(), 1) if label else "",
            description=hint(where, label, side),
        )

    def page_crop(self, document_id: uuid.UUID, page: int, box: list[float]) -> bytes:
        """这一页上 box（和页面图片一样的坐标）这一块放大后的 PNG（ADR 0042）：审核页在候选旁边
        显示局部图。PDF 按局部放大再识别时的倍数重新画，图片用原图。"""
        from jacara.pipeline.region import crop_region
        from jacara.pipeline.zoom import ZOOM_SCALE
        from jacara.plugins.ocr.loader import PDF_SCALE

        self.page_image(document_id, page)  # 检查权限、页码、文件
        document = self._find(document_id)
        data = self.storage.get(document.storage_key or "")
        is_pdf = document.content_type == "application/pdf"
        try:
            image = (
                page_png(data, is_pdf, page, scale=ZOOM_SCALE)
                if is_pdf
                else page_png(data, False, 1)
            )
            factor = ZOOM_SCALE / PDF_SCALE if is_pdf else 1.0
            crop = crop_region(image, [v * factor for v in box])
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        out = io.BytesIO()
        crop.save(out, format="PNG")
        return out.getvalue()

    def ocr_region(self, document_id: uuid.UUID, page: int, box: list[float]) -> RegionOut:
        """把页面上的一块区域裁下来重新 OCR。要能看文档，也要能改字段（不然认出来也没用）。"""
        document = self._find(document_id)
        require(self.owner, "value:review", document.team_id)
        if self.ocr is None:
            raise DocumentError("unavailable", "OCR 引擎没有准备好")
        image = self.page_image(document_id, page)
        try:
            text, confidence = read_region(image, box, self.ocr)
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        return RegionOut(text=text, confidence=confidence)

    def look_region(self, document_id: uuid.UUID, page: int, data: LookIn) -> LookOut:
        """让模型看页面上的一小块（ADR 0038）：手写、印章、被笔迹盖住的字，OCR 认不出时用。
        只返回读出来的，不保存；人确认后再用"审核字段"保存。"""
        document = self._find(document_id)
        require(self.owner, "value:review", document.team_id)
        check = getattr(self.model, "sees_images", None)
        if self.model is None or not callable(check) or not check():
            raise DocumentError(
                "unavailable",
                "模型现在不能看图：把视觉模型 mmproj-F16.gguf 放进 models/，重新 jacara start",
            )
        image = self.page_image(document_id, page)
        try:
            picture = region_png(image, data.box)
        except ValueError as err:
            raise DocumentError("invalid", str(err)) from err
        try:
            text, value, model = look_at(self.model, picture, data.label, data.type)
        except (ModelBackendError, ExtractionError) as err:
            raise DocumentError("unavailable", str(err)) from err
        return LookOut(text=text, value=value, model=model)

    def _enqueue(self, document: Document) -> bool:
        """先提交，再放进队列。

        顺序不能反：worker 是另一个进程，用自己的数据库连接去查这个文档。
        如果先放进队列，worker 可能在提交之前就来查，会找不到。
        队列连不上不算上传失败：文件和记录都在，状态停在 uploaded，之后可以调用重新处理。
        """
        self.session.commit()
        try:
            self.queue.enqueue_document(document.id)
        except QueueError:
            log.warning("文档 %s 没能放进队列，状态保持 uploaded", document.id)
            return False
        return True

    def _find(self, document_id: uuid.UUID) -> Document:
        document = self.repo.get(document_id, self.owner.team_id)
        if document is None:
            raise DocumentError("not_found", "找不到这个文档")
        return document


SIDES = {
    "inline": "后面",
    "colon": "右边",
    "label_left": "右边",
    "row_label": "这一行右边",
    "colon_below": "下面",
    "header_above": "下面",
    "two_column_table": "右边",
}
"""版面分析的配对规则 → 值在标签的哪边（框选建模板写说明用）。"""


def structure_view(data: dict[str, Any] | None, page: int = 1, first: int = 1) -> PageStructureOut:
    """存下来的结构 → 画在原图上用的样子：合并的格子画成一个，空格子不画。

    几页拼成的单据（ADR 0036）：只取第 page 页上的部分，坐标换回这一页自己的。
    """
    if not data:
        return PageStructureOut(available=False)
    part = page_slice(data, page, first)
    if part is not None:
        data = _one_page(data, *part)
    blocks = {b["id"]: b for b in data.get("blocks", [])}
    regions = [
        StructureRegion(kind=blocks[a]["attrs"].get("kind", ""), box=blocks[a]["box"])
        for a in data.get("layouts", [])
        if a in blocks
    ]
    tables: list[StructureTable] = []
    for t in data.get("tables", []):
        table = blocks.get(t)
        if table is None:
            continue
        attrs = table["attrs"]
        covered: set[str] = set()
        cells: list[StructureCell] = []
        for m in attrs.get("merged", []):
            merged = blocks[m]
            covered.update(merged["children"])
            a = merged["attrs"]
            cells.append(
                StructureCell(
                    row=a["row"],
                    col=a["col"],
                    row_span=a["row_span"],
                    col_span=a["col_span"],
                    box=merged["box"],
                    text=merged["text"],
                )
            )
        for c in table["children"]:
            cell = blocks[c]
            if c in covered or cell["attrs"].get("empty"):
                continue
            cells.append(
                StructureCell(
                    row=cell["attrs"]["row"],
                    col=cell["attrs"]["col"],
                    box=cell["box"],
                    text=cell["text"],
                )
            )
        tables.append(
            StructureTable(
                box=table["box"],
                rows=attrs["rows"],
                cols=attrs["cols"],
                by=attrs["by"],
                cells=cells,
            )
        )
    pairs = []
    for k in data.get("keys", []):
        key = blocks[k]
        value = blocks[key["attrs"]["value"]]
        pairs.append(
            StructurePair(
                key=key["text"],
                key_box=key["box"],
                value=value["text"],
                value_box=value["box"],
                rule=key["attrs"].get("rule", ""),
            )
        )
    return PageStructureOut(
        available=True,
        sources=data.get("sources") or {},
        regions=regions,
        tables=tables,
        pairs=pairs,
    )


def _one_page(data: dict[str, Any], top: float, bottom: float) -> dict[str, Any]:
    """几页拼成的结构里，中心落在 [top, bottom) 这一段的块，坐标往上挪 top。
    跨页的表格：只留这一页上的格子，表格的框缩到这些格子。"""

    def inside(box: list[float]) -> bool:
        return top <= (box[1] + box[3]) / 2 < bottom

    def moved(box: list[float]) -> list[float]:
        return [box[0], box[1] - top, box[2], box[3] - top]

    blocks: list[dict[str, Any]] = []
    kept: set[str] = set()
    for b in data.get("blocks", []):
        if b["type"] == "TABLE":
            continue
        if inside(b["box"]):
            blocks.append({**b, "box": moved(b["box"])})
            kept.add(b["id"])
    tables: list[str] = []
    for b in data.get("blocks", []):
        if b["type"] != "TABLE":
            continue
        children = [c for c in b["children"] if c in kept]
        if not children:
            continue
        boxes = [next(x["box"] for x in blocks if x["id"] == c) for c in children]
        box = [
            min(x[0] for x in boxes),
            min(x[1] for x in boxes),
            max(x[2] for x in boxes),
            max(x[3] for x in boxes),
        ]
        attrs = {**b["attrs"], "merged": [m for m in b["attrs"].get("merged", []) if m in kept]}
        blocks.append({**b, "box": box, "children": children, "attrs": attrs})
        tables.append(b["id"])
    keys = [
        k
        for k in data.get("keys", [])
        if k in kept and next(b for b in blocks if b["id"] == k)["attrs"].get("value") in kept
    ]
    return {
        **data,
        "blocks": blocks,
        "layouts": [a for a in data.get("layouts", []) if a in kept],
        "tables": tables,
        "keys": keys,
    }


def _clear_skip(document: Document) -> None:
    document.skipped_at = None
    document.skip_reason = None
    document.skipped_by = None


def _runs(document: Document) -> list[RunOut]:
    """处理过的每一轮，新的在前。"""
    firsts: dict[int, Extraction] = {}
    for e in document.all_extractions:
        firsts.setdefault(e.run, e)
    return [
        RunOut(
            run=run,
            template=e.template,
            template_title=_template_title(e),
            template_version=e.template_version,
            reason=e.run_reason,
            created_at=e.created_at,
            current=run == document.current_run,
        )
        for run, e in sorted(firsts.items(), reverse=True)
    ]


def range_out(value: str | None, day_first: bool | None) -> RangeOut | None:
    """日期范围拆开（ADR 0039）：审核页显示"2014-07-01 ~ 2014-07-31（31 天）"。"""
    if not value:
        return None
    parsed = parse_date_range(value, day_first)
    found = parsed.range
    return RangeOut(
        start=found.start.isoformat() if found and found.start else None,
        end=found.end.isoformat() if found and found.end else None,
        days=found.days if found else None,
        problem=parsed.problem,
    )


def _template_title(extraction: Extraction) -> str:
    if extraction.template == DISCOVER:
        return "抽取所有键值对"
    template = template_of(extraction)
    return template.title if template else extraction.template


def _with_states(
    out: ExtractionOut,
    extraction: Extraction,
    previous: Extraction | None = None,
    session: Session | None = None,
) -> ExtractionOut:
    """按当前值重新检查规则，再给每个字段标上校验状态和颜色（ADR 0028）、中文名，
    以及能沿用的上一轮审核（previous 是同一页上一轮的结果）。
    有 session 时再比一比对象（同一个病人、同一家公司）和别的单据对不对得上（ADR 0031）。"""
    checks = current_checks_json(extraction)
    template = template_of(extraction) if session is not None else None
    if session is not None and template is not None and template.entities:
        issues = cast(list[dict[str, Any]], checks["issues"])
        checks = {**checks, "issues": issues + entity_conflicts(session, extraction, template)}
    states = field_states(extraction, checks)
    labels = labels_of(extraction)
    hints = suggestions(extraction, previous)
    by_id = {v.id: v for v in extraction.values}
    # 更新前处理的文档，出处里没存文字，要用这一页的 OCR 结果找（只在需要时才读）
    needs_ocr = any(wants_source(v) and "text" not in (v.evidence or {}) for v in extraction.values)
    lines = (extraction.ocr or {}).get("lines") if needs_ocr else None
    shape = template if template is not None else template_of(extraction)
    day_first = shape.day_first if shape is not None else None
    # 字段名的别的语言（ADR 0048）：第一个是默认的中文
    languages = list(shape.languages) if shape is not None else []
    others = {lang: labels_of(extraction, lang) for lang in languages[1:]}
    values = [
        v.model_copy(
            update={
                "range": range_out(v.current_value, day_first) if v.type == "date_range" else None,
                "check_state": states[v.id][0],
                "level": states[v.id][1],
                "source_value": source_value(by_id[v.id], evidence_text(by_id[v.id], lines)),
                "label": label_for(labels, v.name),
                "names": {
                    lang: found
                    for lang, names in others.items()
                    if (found := label_for(names, v.name)) is not None
                },
                "suggestion": (
                    SuggestionOut(action=hints[v.id][0], value=hints[v.id][1], run=hints[v.id][2])
                    if v.id in hints
                    else None
                ),
            }
        )
        for v in out.values
    ]
    notes = [
        f"第 {page} 页：{data['note']}"
        for page, data in pages_json(extraction.ocr, extraction.page)
        if data.get("note")
    ]
    return out.model_copy(
        update={
            "current_checks": checks,
            "values": values,
            "labels": labels,
            "languages": languages if len(languages) > 1 else [],
            "label_sets": others,
            "template_title": _template_title(extraction),
            "source_notes": notes,
        }
    )


def _count_pages(content_type: str, data: bytes) -> int:
    if content_type != "application/pdf":
        return 1
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception as err:
        raise DocumentError("unreadable", "PDF 文件打不开，可能已损坏") from err


def _layout_template(documents: list[Document]) -> Template | None:
    """这几份文档都用同一个模板（同一版），而且它有原表样式：返回这个模板。"""
    templates = {(e.template, e.template_version): e for d in documents for e in d.extractions[:1]}
    if len(templates) != 1 or any(not d.extractions for d in documents):
        return None
    template = template_of(next(iter(templates.values())))
    return template if template is not None and template.workbook else None


def _doc_rows(documents: list[Document], template: Template) -> list[DocRow]:
    """审核之后的值 → 填原表用的每一行。设成不导出的字段留空。"""
    hidden = {f.name for f in template.fields if not f.export}
    for t in template.tables:
        hidden |= {f"{t.name}.{c.name}" for c in t.columns if not c.export or not t.export}
    rows: list[DocRow] = []
    for document in documents:
        for report in load_reports(document):
            row = DocRow(
                filename=document.filename,
                page=report.page,
                uploaded_at=document.created_at,
                processed_at=document.processed_at,
                reviewed_at=document.reviewed_at,
            )
            for a in report.aligned:
                value = cell_value(a, report.reviews.get(a.field.name))
                cell = split_cell_name(a.field.name)
                if cell is None:
                    if a.field.name not in hidden:
                        row.values[a.field.name] = value
                    continue
                table, index, column = cell
                items = row.tables.setdefault(table, [])
                while len(items) < index:
                    items.append({})
                if f"{table}.{column}" not in hidden:
                    items[index - 1][column] = value
            rows.append(row)
    return rows


def _workbook(documents: list[Document], language: str | None = None) -> bytes:
    """几个文档写进一个 Excel，返回文件内容。language：列名用哪种语言（ADR 0048）。"""
    reports = [report for d in documents for report in load_reports(d, language)]
    exporter = create_exporter("excel")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"export{exporter.suffix}"
        exporter.export(reports, path)
        return path.read_bytes()


def _unique(name: str, used: set[str]) -> str:
    """zip 里不能有两个同名文件：receipt.xlsx 已经有了，就叫 receipt (2).xlsx。"""
    stem, suffix = PurePath(name).stem, PurePath(name).suffix
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{stem} ({n}){suffix}"
    used.add(candidate)
    return candidate


def clean_folder(folder: str | None) -> str | None:
    """批量导入的文件夹：统一用 / 分隔，去掉开头结尾的 / 和 . 、..，最长 255 个字。空的是 None。"""
    if not folder:
        return None
    parts = [p.strip() for p in folder.replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..")]
    return "/".join(parts)[:255] or None


def check_split(split: str | None) -> str:
    """检查"一个文件里有几张单据"的写法（ADR 0036），写错了说清楚。"""
    try:
        return normalize_split(split)
    except SplitError as err:
        raise DocumentError("invalid", str(err)) from err
