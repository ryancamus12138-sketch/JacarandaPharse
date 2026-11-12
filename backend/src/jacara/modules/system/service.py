"""系统信息。只读：只查不改，不显示任何密码（.env 只说"改没改过"）。"""

from __future__ import annotations

import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jacara import __version__
from jacara.core.config import Settings
from jacara.core.health import run_checks
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.models import Document, ExtractedValue, Extraction, ValueRevision
from jacara.modules.exports.repository import ExportRepository
from jacara.modules.system.schemas import (
    DataInfo,
    ModelInfo,
    OcrInfo,
    ServiceCheck,
    SystemInfo,
    TemplateInfo,
)
from jacara.plugins.ocr.base import OcrConfig
from jacara.templates import load_template, template_names

PROPS_TIMEOUT_S = 2


class SystemService:
    def __init__(self, session: Session, owner: Owner, settings: Settings) -> None:
        self.session = session
        self.owner = owner
        self.settings = settings

    def info(self) -> SystemInfo:
        require(self.owner, "document:read")
        return SystemInfo(
            version=__version__,
            mode=self.settings.jacara_mode,
            model=self._model(),
            ocr=_ocr(),
            templates=[
                TemplateInfo(name=t.name, title=t.title, version=t.version)
                for t in (load_template(n) for n in template_names())
            ],
            data=self._data(self.owner.team_id),
            services=[
                ServiceCheck(name=c.name, ok=c.ok, detail=c.detail, hint=c.hint)
                for c in run_checks(self.settings)
            ],
        )

    def _model(self) -> ModelInfo:
        found = self.settings.find_model()
        props = model_props(self.settings.model_server)
        path = props.get("model_path")
        settings = props.get("default_generation_settings") or {}
        context = props.get("n_ctx") or settings.get("n_ctx")
        return ModelInfo(
            server=self.settings.model_server,
            file=found.name if found else None,
            loaded=str(path).replace("\\", "/").rsplit("/", 1)[-1] if path else None,
            context=int(context) if context else None,
        )

    def _data(self, team: uuid.UUID) -> DataInfo:
        def scalar(query: Any) -> int:
            return int(self.session.scalar(query) or 0)

        exports, export_bytes = ExportRepository(self.session).total_bytes(team)
        return DataInfo(
            documents=scalar(select(func.count()).where(Document.team_id == team)),
            pages=scalar(select(func.sum(Document.page_count)).where(Document.team_id == team)),
            fields=scalar(
                select(func.count(ExtractedValue.id))
                .join(Extraction, ExtractedValue.extraction_id == Extraction.id)
                .where(Extraction.team_id == team)
            ),
            revisions=scalar(select(func.count()).where(ValueRevision.team_id == team)),
            document_bytes=scalar(
                select(func.sum(Document.size_bytes)).where(Document.team_id == team)
            ),
            exports=exports,
            export_bytes=export_bytes,
        )


def model_props(server: str) -> dict[str, Any]:
    """问模型服务（llama-server 的 /props）现在加载的是哪个模型、上下文多长。连不上返回空。"""
    try:
        response = httpx.get(f"{server}/props", timeout=PROPS_TIMEOUT_S)
        data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ocr() -> OcrInfo:
    engine = OcrConfig().engine
    try:
        return OcrInfo(engine=engine, version=version(engine))
    except PackageNotFoundError:
        return OcrInfo(engine=engine, version=None)
