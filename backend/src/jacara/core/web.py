"""让 API 顺便提供网页：把 web/dist 里打包好的前端文件发给浏览器。

这样使用时只要打开 http://127.0.0.1:8000，不用再运行 npm run dev。
开发时照样用 npm run dev（5173，改了马上看到），这里只服务 npm run build 打包出来的东西。

规则：
- /api/... 永远是接口。接口不存在就是 404 JSON，不会变成网页。
- dist 里真有这个文件（例如 /assets/index-3f2a.js、/favicon.svg）就发这个文件。
- 其他网址（例如 /documents/123）都发 index.html，由前端的路由决定显示哪个页面。
  这叫 SPA 回退：没有它，在 /documents/123 按刷新会得到 404。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse, Response

NOT_BUILT = "网页还没有打包。在项目里运行：cd web && npm install && npm run build，然后重启。"

_IMMUTABLE = "public, max-age=31536000, immutable"
"""assets/ 里的文件名带内容指纹（index-3f2a.js），内容一变名字就变，所以浏览器可以一直缓存。"""

_NO_CACHE = "no-cache"
"""index.html 每次都要问一下有没有新版本，否则更新后浏览器还在用旧页面。"""


def mount_web(app: FastAPI, dist: Path) -> None:
    """在所有接口之后加一条"兜底"路由。必须最后调用：前面的路由先匹配。"""
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这个接口")

        index = root / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        file = _inside(root, path)
        if file is not None:
            cache = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": _NO_CACHE})


def _inside(root: Path, path: str) -> Path | None:
    """dist 里的这个文件。不存在、或者网址想跳出 dist（../../.env）都返回 None。"""
    if not path:
        return None
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        return None
    return file
