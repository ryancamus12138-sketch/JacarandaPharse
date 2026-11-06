"""文档的业务逻辑：上传、列表、详情。

权限检查、状态变化只写在这里（项目分层规则）。service 不知道 HTTP，
抛出的是业务错误（DocumentError），由 router 翻译成 HTTP 状态码。
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import uuid
from pathlib import PurePath

from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document
from jacara.modules.documents.repository import DocumentRepository
from jacara.modules.documents.schemas import DocumentDetail, DocumentOut, DocumentPage

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
    def __init__(self, session: Session, storage: ObjectStorage, owner: Owner) -> None:
        self.repo = DocumentRepository(session)
        self.storage = storage
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
        reviews = sum(v.needs_review for e in detail.extractions for v in e.values)
        return detail.model_copy(update={"review_count": reviews})

    def download(self, document_id: uuid.UUID) -> tuple[Document, bytes]:
        document = self._find(document_id)
        require(self.owner, "document:download", document.team_id)
        if document.storage_key is None:
            raise DocumentError("no_file", "这个文档没有保存原始文件（命令行导入的）")
        try:
            return document, self.storage.get(document.storage_key)
        except StorageError as err:
            raise DocumentError("storage", "对象存储暂时不可用，请稍后再试") from err

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
