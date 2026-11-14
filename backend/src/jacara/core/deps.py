"""FastAPI dependencies: per-request resources (database session, object storage, task queue,
current user).

Routers declare `session: Session = Depends(get_session)` and FastAPI calls these functions for
each request. Tests replace them through app.dependency_overrides without changing application
code."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from jacara.core.config import get_settings
from jacara.core.db import session_scope
from jacara.core.i18n import tr
from jacara.core.owner import PERSONAL_OWNER, Owner
from jacara.core.queue import CeleryQueue, TaskQueue
from jacara.core.storage import ObjectStorage
from jacara.core.storage import get_storage as _get_storage
from jacara.modules.auth.service import AuthService

if TYPE_CHECKING:
    from jacara.plugins.model_backends import ModelBackend
    from jacara.plugins.ocr.base import OcrEngine

bearer = HTTPBearer(auto_error=False, description="登录接口返回的 token")
"""Extract the token from the `Authorization: Bearer <token>` header. This is what the Authorize
button on /docs sets."""


def get_session() -> Iterator[Session]:
    """One database session per request: commits on success and rolls back on error."""
    with session_scope() as session:
        yield session


def get_storage() -> ObjectStorage:
    return _get_storage()


@lru_cache
def get_ocr() -> OcrEngine:
    """OCR engine used by the API (for re-recognizing a region during review). Loaded on first use
    and reused afterwards."""
    from jacara.plugins.ocr import create_ocr_engine

    return create_ocr_engine()


def get_model() -> ModelBackend:
    """Local LLM client used by the API (for asking the model about a region during review)."""
    from jacara.core.config import model_api_key
    from jacara.plugins.model_backends import ModelConfig, create_model_backend

    config = ModelConfig(base_url=get_settings().model_server, api_key=model_api_key())
    return create_model_backend(config)


def get_queue() -> TaskQueue:
    return CeleryQueue()


def get_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str | None:
    return credentials.credentials if credentials else None


def get_owner(
    session: Annotated[Session, Depends(get_session)],
    token: Annotated[str | None, Depends(get_token)],
) -> Owner:
    """The current actor.

        Personal mode: no login; always PERSONAL_OWNER.
        Team mode: the user is looked up from the token; a missing or invalid token yields 401
    (login required)."""
    if get_settings().jacara_mode == "personal":
        return PERSONAL_OWNER

    owner = AuthService(session).authenticate(token) if token else None
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr("请先登录") if token is None else tr("登录已失效，请重新登录"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return owner
