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
from datetime import date
from pathlib import Path, PurePath

from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.queue import QueueError, TaskQueue
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document
from jacara.modules.audit.service import record
from jacara.modules.documents.repository import DocumentRepository
from jacara.modules.documents.schemas import DocumentDetail, DocumentOut, DocumentPage
from jacara.pipeline.load import load_reports
from jacara.plugins.exporters import create_exporter
from jacara.plugins.ocr.loader import page_png

log = logging.getLogger(__name__)

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


class DocumentError(Exception):
    """业务错误。code 让 router 知道该返回哪种 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentService:
    def __init__(
        self, session: Session, storage: ObjectStorage, queue: TaskQueue, owner: Owner
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.storage = storage
        self.queue = queue
        self.owner = owner

    def upload(self, filename: str, content_type: str, data: bytes) -> DocumentOut:
        require(self.owner, "document:create")
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
                user_id=self.owner.user_id,
                team_id=self.owner.team_id,
            )
        )
        record(
            self.session, self.owner, "document:upload", "document", document.id, filename=safe_name
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

    def page(self, limit: int, offset: int) -> DocumentPage:
        require(self.owner, "document:read")
        documents, total = self.repo.page(self.owner.team_id, limit, offset)
        counts = self.repo.review_counts([d.id for d in documents])
        items = [
            DocumentOut.model_validate(d).model_copy(update={"review_count": counts.get(d.id, 0)})
            for d in documents
        ]
        return DocumentPage(items=items, total=total)

    def get(self, document_id: uuid.UUID) -> DocumentDetail:
        document = self._find(document_id)
        require(self.owner, "document:read", document.team_id)
        detail = DocumentDetail.model_validate(document)
        reviews = sum(v.pending_review for e in detail.extractions for v in e.values)
        return detail.model_copy(update={"review_count": reviews})

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
        record(self.session, self.owner, "document:review", "document", document.id)
        return DocumentOut.model_validate(document)

    def export(self, document_ids: list[uuid.UUID]) -> tuple[str, bytes]:
        """把一个或多个文档导出成一个 Excel（汇总表一行一张单据），返回（文件名，文件内容）。

        格子里是审核之后的值（current_value）；改过的标蓝，批注里写原值和原因。
        """
        documents = self._exportable(document_ids)
        data = _workbook(documents)
        if len(documents) == 1:
            name = f"{PurePath(documents[0].filename).stem}.xlsx"
        else:
            name = f"jacara-{date.today():%Y%m%d}-{len(documents)}份.xlsx"
        self._record_export(documents, "xlsx")
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
                archive.writestr(name, _workbook([document]))
        self._record_export(documents, "zip")
        return f"jacara-{date.today():%Y%m%d}-{len(documents)}份.zip", out.getvalue()

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

    def _record_export(self, documents: list[Document], file_format: str) -> None:
        # 导出是"数据离开系统"，要留记录：谁、什么时候、导出了哪些文档、什么格式
        record(
            self.session,
            self.owner,
            "document:export",
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


def _count_pages(content_type: str, data: bytes) -> int:
    if content_type != "application/pdf":
        return 1
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception as err:
        raise DocumentError("unreadable", "PDF 文件打不开，可能已损坏") from err


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
