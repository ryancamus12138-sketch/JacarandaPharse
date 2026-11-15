"""Advice worded for how this instance runs. In the desktop app the user restarts JacarandaParse or
uses the settings pages; run from source, the user types commands in a terminal. Callers mark both
wordings for translation (N_) and pick one here."""

from __future__ import annotations

from jacara.core.config import get_settings


def pick(desktop: str, source: str) -> str:
    """The desktop wording inside the desktop app, otherwise the source wording. Both arguments are
    message ids; translate the result with tr() or store it with stored()."""
    return desktop if get_settings().jacara_desktop else source
