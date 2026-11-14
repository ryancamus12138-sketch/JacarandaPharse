"""Serve the built frontend (web/dist) from the API.

Users open http://127.0.0.1:8000 without running `npm run dev`. Development still uses `npm run
dev` (port 5173, hot reload); this module serves only the output of `npm run build`.

Rules:
- /api/... is always the API. Unknown API paths return a JSON 404 and never fall through to the
  frontend.
- Files that exist in dist (for example /assets/index-3f2a.js, /favicon.svg) are served as is.
- Every other path (for example /documents/123) serves index.html and the frontend router selects
  the page. This is the SPA fallback; without it, reloading /documents/123 returns 404."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse, Response

from jacara.core.i18n import N_, tr

NOT_BUILT = N_("网页尚未构建。请在项目目录运行：cd web && npm install && npm run build，然后重启。")

_IMMUTABLE = "public, max-age=31536000, immutable"
"""File names in assets/ carry a content hash (index-3f2a.js) that changes with the content, so
browsers may cache them indefinitely."""

_NO_CACHE = "no-cache"
"""index.html is always revalidated; otherwise browsers keep using the old page after an update."""


def mount_web(app: FastAPI, dist: Path) -> None:
    """Add the catch-all route after all API routes. Must be called last so that earlier routes
    match first."""
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, tr("接口不存在"))

        index = root / "index.html"
        if not index.is_file():
            return PlainTextResponse(tr(NOT_BUILT), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        file = _inside(root, path)
        if file is not None:
            cache = _IMMUTABLE if path.startswith("assets/") else _NO_CACHE
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": _NO_CACHE})


def _inside(root: Path, path: str) -> Path | None:
    """The file in dist for this path. None if it does not exist or the path escapes dist
    (../../.env)."""
    if not path:
        return None
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        return None
    return file
