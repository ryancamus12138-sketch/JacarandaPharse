"""导入所有数据库表。

表之间用名字互相引用（例如 Document.extractions → "Extraction"），
SQLAlchemy 要在所有表都导入之后才能把它们连起来。用到数据库之前 import 这个模块即可。
新增一个模块的 models.py，也要在这里加一行。
"""

from jacara.core.db import Base
from jacara.modules.audit.models import AuditLog
from jacara.modules.auth.models import AuthSession, PasswordResetCode, Team, User
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.exports.models import Export
from jacara.modules.fields.models import ExtractedValue, ValueRevision

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "Document",
    "Export",
    "ExtractedValue",
    "Extraction",
    "PasswordResetCode",
    "Team",
    "User",
    "ValueRevision",
]
