"""Interface language of web responses.

- The language of each request is negotiated from Accept-Language (RFC 9110 section 12.5.4) by
  LanguageMiddleware and kept in a context variable, so concurrent requests do not interfere.
- Message ids are the Chinese source text (the gettext msgid model). Chinese needs no catalog;
  English translations live in locale/en/LC_MESSAGES/messages.po, compiled to messages.mo with
  Babel (pybabel extract / update / compile). Only the .mo file is needed at runtime.
- Messages written by background workers and displayed later are stored as a template plus
  parameters (stored()) and translated when a response is built (render()), because the reader's
  language is unknown at write time.
"""

from __future__ import annotations

import gettext
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Annotated, Any, overload

from pydantic import AfterValidator
from starlette.types import ASGIApp, Receive, Scope, Send

LANGUAGES = ("zh", "en")
"""Supported interface languages; the first is the source language of message ids."""

DEFAULT = "zh"

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locale"

_language: ContextVar[str] = ContextVar("jacara_language", default=DEFAULT)

_STORED_PREFIXES = ('{"msg":', '{"join":')
"""How stored messages start: stored() and joined() output."""


def language() -> str:
    """Language of the current request (or of the current use_language block)."""
    return _language.get()


@contextmanager
def use_language(code: str) -> Iterator[None]:
    """Run a block with the given language (tests, exports in a requested language)."""
    token = _language.set(code if code in LANGUAGES else DEFAULT)
    try:
        yield
    finally:
        _language.reset(token)


@cache
def _catalog(code: str) -> gettext.NullTranslations:
    if code == DEFAULT:
        return gettext.NullTranslations()
    return gettext.translation("messages", localedir=LOCALE_DIR, languages=[code], fallback=True)


def tr(message: str, /, **params: Any) -> str:
    """Translate message into the current language, then substitute {name} placeholders.

    The whole sentence is translated before values are inserted, so translations can reorder them.
    Parameters that are stored messages (stored()) are rendered first.
    """
    if not message:
        return message  # gettext("") returns the catalog header.
    text = _catalog(language()).gettext(message)
    return text.format(**_render_params(params)) if params else text


def trc(context: str, message: str, /, **params: Any) -> str:
    """tr() with a message context (gettext pgettext), for a word translated differently depending
    on its use, such as a sheet name and a column header."""
    if not message:
        return message
    text = _catalog(language()).pgettext(context, message)
    return text.format(**_render_params(params)) if params else text


def ntr(singular: str, plural: str, n: int, /, **params: Any) -> str:
    """Plural-aware tr(); n is also available as {n}."""
    text = _catalog(language()).ngettext(singular, plural, n)
    return text.format(n=n, **_render_params(params))


def variants(message: str) -> frozenset[str]:
    """A message id together with its translation in every supported language.

    Used to read files whose fixed labels were written in any interface language (for example the
    standard template workbook)."""
    return frozenset(_catalog(code).gettext(message) for code in LANGUAGES)


def variants_c(context: str, message: str) -> frozenset[str]:
    """variants() for a message with a context (see trc)."""
    return frozenset(_catalog(code).pgettext(context, message) for code in LANGUAGES)


def N_c(context: str, message: str) -> str:
    """N_() for a message with a context; translate later with trc(context, value)."""
    del context
    return message


def N_(message: str) -> str:
    """Mark a string for extraction without translating it (gettext_noop; recognized by Babel).

    Translate later with tr(value).
    """
    return message


def stored(message: str, /, **params: Any) -> str:
    """Encode a message for storage as template plus parameters; render() translates it on display.

    Parameters that are themselves stored messages are embedded as JSON objects (not as escaped
    strings, whose size would double with every level) and rendered recursively. Values that are
    not JSON types (paths, exceptions) are stored as their str().
    """
    body: dict[str, Any] = {"msg": message}
    if params:
        body["params"] = {key: _embed(val) for key, val in params.items()}
    return json.dumps(body, ensure_ascii=False, default=str)


NOTE_SEPARATOR = N_("；")
"""Separator between notes (Chinese "；", English "; ")."""

ITEM_SEPARATOR = N_("、")
"""Separator between list items (Chinese "、", English ", ")."""


def joined(separator: str, items: Iterable[str]) -> str:
    """Several messages (stored messages, message ids or plain text) written one after another; the
    separator is a message id translated on display. Empty items are skipped.

    Stored flat as {"join": separator, "items": [...]}, so long lists stay small and shallow.
    """
    parts = [item for item in items if item]
    if not parts:
        return ""
    if len(parts) == 1:
        return nested(parts[0])
    body = {"join": separator, "items": [_embed(nested(part)) for part in parts]}
    return json.dumps(body, ensure_ascii=False, default=str)


def join_notes(*notes: str) -> str:
    """Join notes into one stored message, separated by "；" in Chinese and "; " in English."""
    return joined(NOTE_SEPARATOR, notes)


def join_items(items: Iterable[str]) -> str:
    """Join list items into one stored message, separated by "、" in Chinese and ", " in English."""
    return joined(ITEM_SEPARATOR, items)


def is_stored(value: str) -> bool:
    """Whether value is a stored message (stored() or joined()) rather than plain text."""
    return value.startswith(_STORED_PREFIXES)


def nested(value: str) -> str:
    """A message id used as a parameter is wrapped so render() translates it too."""
    return value if is_stored(value) else stored(value)


def _embed(value: Any) -> Any:
    """Parameter value for storage: stored messages become JSON objects."""
    if isinstance(value, str) and is_stored(value):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@overload
def render(value: str) -> str: ...
@overload
def render(value: None) -> None: ...
def render(value: str | None) -> str | None:
    """Translate a stored message for the current language.

    Plain text (written before messages were stored structurally, or free text entered by users) is
    translated when it matches a message id exactly and returned unchanged otherwise.
    """
    if not value:
        return value
    if is_stored(value):
        try:
            return _render_object(json.loads(value))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            return value
    return _catalog(language()).gettext(value)


def _render_object(data: dict[str, Any]) -> str:
    if "join" in data:
        separator = tr(str(data["join"]))
        return separator.join(_render_value(item) for item in data.get("items") or [])
    params = {key: _render_value(val) for key, val in dict(data.get("params") or {}).items()}
    return tr(str(data["msg"]), **params)


def _render_value(value: Any) -> Any:
    if isinstance(value, dict) and ("msg" in value or "join" in value):
        return _render_object(value)
    if isinstance(value, str) and is_stored(value):
        return render(value)
    return value


def _render_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: _render_value(val) for key, val in params.items()}


def _render_text(value: str) -> str:
    return render(value)


Rendered = Annotated[str, AfterValidator(_render_text)]
"""Response schema field holding a stored message or a message id; rendered when the response is
built (inside the request, so in the request language)."""


def render_items(items: Iterable[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    """Copies of JSON objects (from JSON columns) with the given keys rendered."""
    return [
        {**item, **{key: render(item[key]) for key in keys if isinstance(item.get(key), str)}}
        for item in items
    ]


def render_stored(data: dict[str, Any]) -> dict[str, Any]:
    """Copy of a JSON object with every stored-message value rendered (audit log details)."""
    return {
        key: render(val) if isinstance(val, str) and is_stored(val) else val
        for key, val in data.items()
    }


def render_checks(checks: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rule results (checks JSON: issues, passed, skipped) with issue titles and messages
    rendered."""
    if not checks:
        return checks
    return {**checks, "issues": render_items(checks.get("issues") or [], "title", "message")}


def render_candidates(candidates: dict[str, Any] | None) -> dict[str, Any] | None:
    """Field candidates ({"items": [...]}) with their notes rendered."""
    if not candidates:
        return candidates
    return {**candidates, "items": render_items(candidates.get("items") or [], "note")}


def render_classified(classified: dict[str, Any] | None) -> dict[str, Any] | None:
    """Automatic template selection ({"picked", "reason", "by", "candidates"}) with the reason
    rendered."""
    if not classified:
        return classified
    return render_items([classified], "reason")[0]


def render_models(models: dict[str, Any] | None) -> dict[str, Any] | None:
    """OCR model information of a page (engine, source, note, …) with stored values rendered."""
    if not models:
        return models
    return render_items([models], *models.keys())[0]


def negotiate(header: str | None) -> str:
    """Pick the interface language from an Accept-Language header.

    q-values are honored and q=0 excludes a language; unsupported languages fall back to the
    default.
    """
    if not header:
        return DEFAULT
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate(header.split(",")):
        tag, _sep, rest = part.strip().partition(";")
        quality = 1.0
        for param in rest.split(";"):
            key, _eq, val = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(val)
                except ValueError:
                    quality = 0.0
        if tag and quality > 0:
            ranked.append((-quality, index, tag.strip().lower()))
    for _q, _i, tag in sorted(ranked):
        primary = tag.split("-")[0]
        if primary in LANGUAGES:
            return primary
        if tag == "*":
            return DEFAULT
    return DEFAULT


class LanguageMiddleware:
    """ASGI middleware setting the request language from Accept-Language.

    Equivalent to Django's LocaleMiddleware without URL prefixes or cookies.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        header = None
        for key, val in scope.get("headers", []):
            if key == b"accept-language":
                header = val.decode("latin-1")
                break
        token = _language.set(negotiate(header))
        try:
            await self.app(scope, receive, send)
        finally:
            _language.reset(token)
