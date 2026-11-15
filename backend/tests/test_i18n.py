"""Interface language: Accept-Language negotiation, catalogs and stored messages."""

import gettext
import re
from pathlib import Path

from babel.messages.extract import extract_from_dir
from babel.messages.pofile import read_po

from jacara.core.i18n import (
    LOCALE_DIR,
    N_,
    is_stored,
    join_items,
    join_notes,
    language,
    negotiate,
    ntr,
    render,
    stored,
    tr,
    trc,
    use_language,
    variants,
    variants_c,
)

PACKAGE = LOCALE_DIR.parent
KEYWORDS = {
    "tr": None,
    "trc": ((1, "c"), 2),
    "ntr": (1, 2),
    "N_": None,
    "N_c": ((1, "c"), 2),
    "stored": None,
}
"""Same keywords as the pybabel extract command in docs/adr/0065-ui-language.md."""
PLACEHOLDER = re.compile(r"(?<!\{)\{(\w+)[^{}]*\}(?!\})")


def test_negotiate_accept_language() -> None:
    assert negotiate(None) == "zh"
    assert negotiate("") == "zh"
    assert negotiate("en") == "en"
    assert negotiate("en-US,en;q=0.9") == "en"
    assert negotiate("zh-CN,zh;q=0.9,en;q=0.8") == "zh"
    assert negotiate("fr-FR, en;q=0.5") == "en"
    assert negotiate("en;q=0, zh;q=0.1") == "zh"
    assert negotiate("de") == "zh"
    assert negotiate("*") == "zh"


def test_use_language_restores_previous() -> None:
    assert language() == "zh"
    with use_language("en"):
        assert language() == "en"
    assert language() == "zh"
    with use_language("xx"):
        assert language() == "zh"


def test_source_language_returns_message_id() -> None:
    assert tr("共 {n} 页", n=3) == "共 3 页"
    assert ntr("{n} 页", "{n} 页", 2) == "2 页"
    assert N_("原文") == "原文"


def test_stored_messages_render_and_keep_plain_text() -> None:
    value = stored("抽取第 {page} / {pages} 页", page=2, pages=3)
    assert render(value) == "抽取第 2 / 3 页"
    nested = stored("{inner}（第 {n} 次）", inner=stored("文字识别"), n=1)
    assert render(nested) == "文字识别（第 1 次）"
    assert render("用户自己写的原因") == "用户自己写的原因"
    assert render(None) is None
    assert render('{"msg": broken') == '{"msg": broken'


def test_joined_messages_stay_flat() -> None:
    items = [f"item{i}" for i in range(500)]
    value = join_items(items)
    assert is_stored(value) and len(value) < 40 * len(items)  # Linear, not exponential.
    assert render(value) == "、".join(items)
    with use_language("en"):
        assert render(value) == ", ".join(items)
    notes = join_notes(stored("文字识别"), "", stored("保存结果"))
    assert render(notes) == "文字识别；保存结果"
    with use_language("en"):
        assert render(notes) == "Recognizing text; Saving results"
    assert join_notes() == "" and join_items([]) == ""


def test_tr_renders_stored_parameters() -> None:
    error = stored("框选区域过小，请框选需要识别的文字")
    with use_language("en"):
        assert tr("{name}：{message}", name="a.pdf", message=error) == (
            "a.pdf: The box is too small; select the text to recognize"
        )
    assert tr("") == ""


def test_contexts_and_variants() -> None:
    assert trc("sheet", "字段") == "字段"
    with use_language("en"):
        assert trc("sheet", "字段") == "Fields"
        assert tr("字段") == "Field"
    assert variants_c("sheet", "规则") == {"规则", "Rules"}
    assert variants("是") == {"是", "Yes"}


def test_catalog_matches_the_code() -> None:
    """Every message in the code has an English translation with the same placeholders, and the
    compiled catalog is up to date."""
    found: set[tuple[str | None, str]] = set()
    for _file, _line, message, _comments, context in extract_from_dir(
        str(PACKAGE), method_map=[("**.py", "python")], keywords=KEYWORDS
    ):
        assert isinstance(message, str), message
        found.add((context, message))
    path = LOCALE_DIR / "en" / "LC_MESSAGES" / "messages.po"
    with path.open("rb") as file:
        catalog = read_po(file, locale="en")
    entries = {(m.context, m.id): m.string for m in catalog if m.id and isinstance(m.id, str)}
    assert len(entries) == len([m for m in catalog if m.id]), "plural messages are not used"
    assert found - entries.keys() == set(), "run pybabel extract / update and translate"
    assert entries.keys() - found == set(), "obsolete entries: run pybabel update"
    compiled = gettext.translation("messages", localedir=LOCALE_DIR, languages=["en"])
    for (context, message), translation in entries.items():
        assert isinstance(translation, str) and translation, message
        assert sorted(PLACEHOLDER.findall(message)) == sorted(PLACEHOLDER.findall(translation)), (
            message
        )
        got = compiled.pgettext(context, message) if context else compiled.gettext(message)
        assert got == translation, f"messages.mo is out of date: run pybabel compile ({message})"


def test_package_ships_the_compiled_catalog() -> None:
    assert (Path(PACKAGE) / "locale" / "en" / "LC_MESSAGES" / "messages.mo").is_file()
