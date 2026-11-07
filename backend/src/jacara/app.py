"""Web 服务：把各模块的接口挂到 /api/v1 下面。

启动：uv run jacara api，然后浏览器打开 http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi_offline import FastAPIOffline

from jacara import __version__
from jacara.modules.documents.router import router as documents_router
from jacara.modules.fields.router import router as fields_router

API_PREFIX = "/api/v1"
"""接口版本号（项目规则 9）：以后不兼容的改动放到 /api/v2，旧的继续能用。"""


def create_app() -> FastAPI:
    # FastAPIOffline：/docs 页面用的界面文件随安装包一起提供，不从网上下载（项目规则 1：离线）。
    app = FastAPIOffline(
        title="JacarandaParse",
        version=__version__,
        description="离线文档结构化：上传收据和发票，抽取字段并核对出处。",
        redoc_url=None,
    )

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(documents_router)
    api.include_router(fields_router)

    @api.get("/health", tags=["系统"], summary="服务是否在运行")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api)
    return app


app = create_app()
