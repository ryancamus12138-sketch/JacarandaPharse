"""Import all database models.

Models reference each other by name (for example Document.extractions → "Extraction"), and
SQLAlchemy can resolve those references only after every model is imported. Import this module
before using the database. When a module adds a models.py, add it here as well."""

from jacara.core.db import Base
from jacara.modules.audit.models import AuditLog
from jacara.modules.auth.models import AuthSession, PasswordResetCode, Profile, Team, User
from jacara.modules.comments.models import Comment
from jacara.modules.documents.models import Document, Extraction
from jacara.modules.exports.models import Export
from jacara.modules.fields.models import ExtractedValue, ValueRevision
from jacara.modules.hub.models import Dataset, OcrEvalRun
from jacara.modules.insights.models import ErrorAttribution
from jacara.modules.ocr_samples.models import (
    DetPage,
    DocGroupMember,
    OcrSample,
    OcrSampleExport,
    PairDecision,
    SplitAssignment,
    TeamSetting,
)
from jacara.modules.templates.models import TemplateRecord, TemplateVersion

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "Comment",
    "Dataset",
    "DetPage",
    "DocGroupMember",
    "Document",
    "ErrorAttribution",
    "Export",
    "ExtractedValue",
    "Extraction",
    "OcrEvalRun",
    "OcrSample",
    "OcrSampleExport",
    "PairDecision",
    "PasswordResetCode",
    "Profile",
    "SplitAssignment",
    "Team",
    "TeamSetting",
    "TemplateRecord",
    "TemplateVersion",
    "User",
    "ValueRevision",
]
