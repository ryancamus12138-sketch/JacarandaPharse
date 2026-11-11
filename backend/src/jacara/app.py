"""Web 服务：把各模块的接口挂到 /api/v1 下面，其他网址交给打包好的网页（web/dist）。

启动：uv run jacara start（API 和后台任务一起），浏览器打开 http://127.0.0.1:8000
接口文档：http://127.0.0.1:8000/docs（.env 里 JACARA_API_DOCS=false 可以关掉）
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi_offline import FastAPIOffline

from jacara import __version__
from jacara.core.config import get_settings
from jacara.core.permissions import PermissionDenied
from jacara.core.web import mount_web
from jacara.modules.audit.router import router as audit_router
from jacara.modules.auth.router import auth_router, users_router
from jacara.modules.documents.router import router as documents_router
from jacara.modules.fields.router import router as fields_router

API_PREFIX = "/api/v1"
"""接口版本号（项目规则 9）：以后不兼容的改动放到 /api/v2，旧的继续能用。"""

TITLE = "JacarandaParse"
ABOUT = "离线文档结构化：上传收据和发票，抽取字段并核对出处。"


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.jacara_api_docs:
        # FastAPIOffline：/docs 页面用的界面文件随安装包一起提供，不从网上下载（项目规则 1：离线）。
        app = FastAPIOffline(redoc_url=None, title=TITLE, version=__version__, description=ABOUT)
    else:
        # 关掉文档：/docs 和 /openapi.json 都不存在，这两个网址会显示前端的页面。
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
    api.include_router(fields_router)
    api.include_router(users_router)
    api.include_router(audit_router)

    @api.get("/health", tags=["系统"], summary="服务是否在运行")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api)

    @app.exception_handler(PermissionDenied)
    def permission_denied(request: Request, err: PermissionDenied) -> JSONResponse:
        """service 里 require() 不通过时抛出 PermissionDenied，这里统一变成 403。"""
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(err)})

    # 一定放在最后：上面的接口和 /docs 先匹配，剩下的网址才交给网页。
    mount_web(app, settings.web_dist)
    return app


app = create_app()
