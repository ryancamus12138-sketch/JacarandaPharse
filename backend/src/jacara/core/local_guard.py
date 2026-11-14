"""Local access guard: only this computer's own pages may use the service.

The service listens on 127.0.0.1, but any web page open in the user's browser can still send
requests to that address, and through DNS rebinding a malicious page can even read the answers.
Three checks, applied to every request:

1. Host: only 127.0.0.1 and localhost are accepted. A DNS rebinding request carries the
   attacker's domain name in the Host header and is refused (the MCP specification requires local
   HTTP servers to defend against DNS rebinding).
2. Cross-site writes: for methods other than GET, HEAD and OPTIONS, Sec-Fetch-Site must be
   same-origin or none; browsers without Fetch Metadata are checked by the Origin header instead
   (OWASP Cross-Site Request Forgery Prevention Cheat Sheet). Requests carrying neither header do
   not come from a web page (command-line tools, tests) and are allowed.
3. Launch token (desktop edition only, when JACARA_DESKTOP_TOKEN is set): the desktop app opens
   /desktop/enter?token=<token>; the service then sets a cookie with the token and redirects to the
   home page, and every later request must carry that cookie (the approach of Jupyter Server: a
   random token generated at start-up, passed once in the URL, then kept in a cookie). Other
   programs on the computer cannot use the service without the token."""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_SITES = frozenset({"same-origin", "none"})
ENTER_PATH = "/desktop/enter"

NOT_FROM_APP = (
    "请从 JacarandaParse 程序窗口中打开。\n"
    "Please open JacarandaParse from its application window.\n"
)


def host_name(host: str) -> str:
    """Host header without the port: 127.0.0.1:8000 → 127.0.0.1, [::1]:8000 → [::1]."""
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


def cookie_name(scope: Scope) -> str:
    """Cookies are shared by every port of a host, so the name includes this service's port."""
    server = scope.get("server")
    port = server[1] if server else 0
    return f"jacara_token_{port}"


class LocalGuard:
    """ASGI middleware implementing the three checks above."""

    def __init__(self, app: ASGIApp, token: str | None = None) -> None:
        self.app = app
        self.token = token or None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        response = self.check(scope)
        if response is not None:
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def check(self, scope: Scope) -> Response | None:
        """The response that refuses this request, or None when it may pass."""
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if host_name(host) not in ALLOWED_HOSTS:
            return PlainTextResponse("Invalid host header", status_code=400)
        if scope.get("method", "GET") not in SAFE_METHODS:
            site = headers.get("sec-fetch-site")
            origin = headers.get("origin")
            if site is not None:
                if site not in ALLOWED_SITES:
                    return PlainTextResponse("Cross-site request refused", status_code=403)
            elif origin is not None and origin != f"http://{host}":
                return PlainTextResponse("Cross-site request refused", status_code=403)
        if self.token is None:
            return None
        if scope["path"] == ENTER_PATH:
            return self.enter(scope)
        cookies = cookie_parser(headers.get("cookie", ""))
        given = cookies.get(cookie_name(scope), "")
        if not secrets.compare_digest(given.encode(), self.token.encode()):
            return PlainTextResponse(NOT_FROM_APP, status_code=401)
        return None

    def enter(self, scope: Scope) -> Response:
        """Exchange the launch token in the URL for a cookie, then open the home page."""
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        given = (query.get("token") or [""])[0]
        if not secrets.compare_digest(given.encode(), self.token.encode() if self.token else b""):
            return PlainTextResponse(NOT_FROM_APP, status_code=401)
        response = RedirectResponse("/", status_code=303)
        # HttpOnly: page scripts cannot read it. SameSite=Strict: never sent with requests that
        # start on another site.
        response.set_cookie(cookie_name(scope), given, httponly=True, samesite="strict", path="/")
        return response
