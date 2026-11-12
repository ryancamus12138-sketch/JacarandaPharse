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
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.queue import QueueError, TaskQueue
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document, Extraction, TemplateVersion
from jacara.modules.audit.service import record
from jacara.modules.documents.repository import DocumentFilter, DocumentRepository
from jacara.modules.documents.schemas import (
    REPROCESS_REASONS,
    Dashboard,
    DashboardCounts,
    DocumentDetail,
    DocumentNeighbors,
    DocumentOut,
    DocumentPage,
    ExtractionOut,
    OcrLineOut,
    PageOcr,
    RegionOut,
    ReprocessIn,
    RunOut,
)
from jacara.modules.exports.service import save_export
from jacara.modules.fields.schemas import AdoptOut, SuggestionOut
from jacara.modules.fields.service import FieldService
from jacara.modules.templates.service import TemplateFailure, TemplateService
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
from jacara.pipeline.region import read_region
from jacara.plugins.exporters import create_exporter
from jacara.plugins.exporters.excel import cell_value
from jacara.plugins.extractors.base import DISCOVER, split_cell_name
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
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.storage = storage
        self.queue = queue
        self.ocr = ocr
        """只有"重新识别这块"用到。"""
        self.owner = owner

    def upload(
        self, filename: str, content_type: str, data: bytes, template: str | None = None
    ) -> DocumentOut:
        """template：我的模板的 id，或者 discover（抽出所有键值对）。不写就用上一次用的。"""
        require(self.owner, "document:create")
        try:
            version_id, discover = TemplateService(self.owner, self.session).pick_for_upload(
                template
            )
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
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                page_count=_count_pages(content_type, data),
                storage_key=key,
                status="uploaded",
                template_version_id=version_id,
                discover=discover,
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
            template="discover" if discover else str(version_id or "receipt"),
        )
        self._enqueue(document)
        return DocumentOut.model_validate(document)

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
                version_id, discover = templates.pick_for_upload(data.template)
            elif document.template_version_id is not None:
                # 同一个模板的最新版（"模板改了"最常见）
                current = self.session.get_one(TemplateVersion, document.template_version_id)
                version_id, discover = templates.latest_version_id(current.template_id), False
            else:
                version_id, discover = None, document.discover
        except TemplateFailure as err:
            raise DocumentError(err.code, err.message) from err

        label = REPROCESS_REASONS[data.reason]
        document.template_version_id = version_id
        document.discover = discover
        document.run_reason = f"{label}：{note}" if note else label
        document.status = "uploaded"
        document.error = None
        document.reviewed_at = None
        record(
            self.session,
            self.owner,
            "document:reprocess",
            "document",
            document.id,
            reason=data.reason,
            note=note,
            template="discover" if discover else str(version_id or "receipt"),
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

    def page(self, limit: int, offset: int, filters: DocumentFilter | None = None) -> DocumentPage:
        require(self.owner, "document:read")
        documents, total = self.repo.page(self.owner.team_id, limit, offset, filters)
        return DocumentPage(items=self._with_counts(documents), total=total)

    def _with_counts(self, documents: list[Document]) -> list[DocumentOut]:
        """文档 + 每份还有几个字段等人确认。"""
        counts = self.repo.review_counts([d.id for d in documents])
        return [
            DocumentOut.model_validate(d).model_copy(update={"review_count": counts.get(d.id, 0)})
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
        extraction = next((e for e in document.extractions if e.page == page), None)
        if extraction is None:
            raise DocumentError("not_found", f"第 {page} 页没有抽取结果")
        lines = (extraction.ocr or {}).get("lines", [])
        return PageOcr(
            available=extraction.ocr is not None,
            lines=[OcrLineOut(index=i, **line) for i, line in enumerate(lines)],
        )

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
        record(self.session, self.owner, "document:review", "document", document.id)
        return DocumentOut.model_validate(document)

    def export(self, document_ids: list[uuid.UUID]) -> tuple[str, bytes]:
        """把一个或多个文档导出成一个 Excel（汇总表一行一张单据），返回（文件名，文件内容）。

        格子里是审核之后的值（current_value）；改过的标蓝，批注里写原值和原因。
        """
        documents = self._exportable(document_ids)
        data, layout = self._excel(documents)
        if len(documents) == 1:
            name = f"{PurePath(documents[0].filename).stem}.xlsx"
        elif layout:
            # 按原表导出：用原表的名字
            name = f"{PurePath(layout).stem}-{date.today():%Y%m%d}-{len(documents)}份.xlsx"
        else:
            name = f"jacara-{date.today():%Y%m%d}-{len(documents)}份.xlsx"
        self._record_export(documents, "xlsx", name, data)
        return name, data

    def export_zip(self, document_ids: list[uuid.UUID]) -> tuple[str, bytes]:
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
                archive.writestr(name, self._excel([document])[0])
        name = f"jacara-{date.today():%Y%m%d}-{len(documents)}份.zip"
        data = out.getvalue()
        self._record_export(documents, "zip", name, data)
        return name, data

    def _excel(self, documents: list[Document]) -> tuple[bytes, str]:
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
        return _workbook(documents), ""

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
    values = [
        v.model_copy(
            update={
                "check_state": states[v.id][0],
                "level": states[v.id][1],
                "source_value": source_value(by_id[v.id], evidence_text(by_id[v.id], lines)),
                "label": label_for(labels, v.name),
                "suggestion": (
                    SuggestionOut(action=hints[v.id][0], value=hints[v.id][1], run=hints[v.id][2])
                    if v.id in hints
                    else None
                ),
            }
        )
        for v in out.values
    ]
    return out.model_copy(
        update={
            "current_checks": checks,
            "values": values,
            "labels": labels,
            "template_title": _template_title(extraction),
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


def _workbook(documents: list[Document]) -> bytes:
    """几个文档写进一个 Excel，返回文件内容。"""
    reports = [report for d in documents for report in load_reports(d)]
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
