"""Web application: mounts module routers under /api/v1 and serves the built frontend (web/dist) for
all other paths.

Start: uv run jacara start (API and workers), then open http://127.0.0.1:8000
API documentation: http://127.0.0.1:8000/docs (disable with JACARA_API_DOCS=false)"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi_offline import FastAPIOffline

from jacara import __version__
from jacara.core.config import get_settings
from jacara.core.i18n import LanguageMiddleware
from jacara.core.permissions import PermissionDenied
from jacara.core.web import mount_web
from jacara.modules.app_settings.router import router as settings_router
from jacara.modules.audit.router import router as audit_router
from jacara.modules.auth.router import auth_router, users_router
from jacara.modules.comments.router import comments_router, document_comments_router
from jacara.modules.documents.router import dashboard_router
from jacara.modules.documents.router import router as documents_router
from jacara.modules.exports.router import router as exports_router
from jacara.modules.fields.router import router as fields_router
from jacara.modules.fields.router import rows_router
from jacara.modules.hub.router import router as hub_router
from jacara.modules.insights.router import router as insights_router
from jacara.modules.model_admin.router import router as models_router
from jacara.modules.ocr_samples.router import router as ocr_samples_router
from jacara.modules.system.router import router as system_router
from jacara.modules.templates.router import router as templates_router

API_PREFIX = "/api/v1"
"""API version prefix. Incompatible changes go to /api/v2 while /api/v1 remains available."""

TITLE = "JacarandaParse"
ABOUT = "离线文档结构化：上传收据和发票，抽取字段并核对出处。"


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.jacara_api_docs:
        # FastAPIOffline serves the /docs assets from the installed package; nothing is fetched from
        # the network.
        app = FastAPIOffline(redoc_url=None, title=TITLE, version=__version__, description=ABOUT)
    else:
        # Docs disabled: /docs and /openapi.json are not registered and fall through to the
        # frontend.
        app = FastAPI(
            title=TITLE,
            version=__version__,
            description=ABOUT,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(auth_router)
    api.include_router(documents_router)
    api.include_router(document_comments_router)
    api.include_router(comments_router)
    api.include_router(dashboard_router)
    api.include_router(exports_router)
    api.include_router(fields_router)
    api.include_router(rows_router)
    api.include_router(users_router)
    api.include_router(audit_router)
    api.include_router(templates_router)
    api.include_router(system_router)
    api.include_router(insights_router)
    api.include_router(ocr_samples_router)
    api.include_router(models_router)
    api.include_router(hub_router)
    api.include_router(settings_router)

    @api.get("/health", tags=["系统"], summary="服务是否在运行")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api)
    app.add_middleware(LanguageMiddleware)

    @app.exception_handler(PermissionDenied)
    def permission_denied(request: Request, err: PermissionDenied) -> JSONResponse:
        """Map PermissionDenied raised by service-layer require() calls to HTTP 403."""
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(err)})

    # Must be registered last: API routes and /docs match first; remaining paths are served by the
    # frontend.
    mount_web(app, settings.web_dist)
    return app


app = create_app()
