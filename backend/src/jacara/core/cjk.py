"""Normalize CJK compatibility characters found in PDF text layers.

Some PDFs (exported by browsers or Word with certain Chinese fonts) encode part of the CJK
ideographs as radical code points: 示 as U+2F70 (Kangxi Radicals), 小 as ⼩, 门 as U+2ED4 (CJK
Radicals Supplement), 民 as ⺠. They render identically but are different characters. The model
reads them as the regular characters, so evidence matching against the source text fails and the
value is rejected. The PDF text layer is therefore normalized on read.

- Kangxi Radicals (U+2F00–U+2FD5) and CJK Compatibility Ideographs (U+F900–U+FAFF): mapped by
  Unicode NFKC.
- CJK Radicals Supplement (U+2E80–U+2EF3): mostly without a standard mapping; the table below
  covers only radicals that are standalone characters with identical glyphs. Component forms
  (variants of 亻, 氵, 扌 and similar) are not mapped.

Only these ranges are normalized; other characters (full-width colons, brackets) are left
unchanged."""

from __future__ import annotations

import unicodedata

_SUPPLEMENT = {
    "⺁": "厂",
    "⺇": "几",
    "⺊": "卜",
    "⺟": "母",
    "⺠": "民",
    "⺩": "王",
    "⺬": "示",
    "⺹": "耂",
    "⻄": "西",
    "⻅": "见",
    "⻆": "角",
    "⻈": "讠",
    "⻉": "贝",
    "⻋": "车",
    "⻓": "长",
    "⻔": "门",
    "⻘": "青",
    "⻙": "韦",
    "⻚": "页",
    "⻛": "风",
    "⻜": "飞",
    "⻝": "食",
    "⻠": "饣",
    "⻡": "首",
    "⻢": "马",
    "⻣": "骨",
    "⻤": "鬼",
    "⻥": "鱼",
    "⻦": "鸟",
    "⻧": "卤",
    "⻨": "麦",
    "⻩": "黄",
    "⻪": "黾",
    "⻬": "齐",
    "⻮": "齿",
    "⻰": "龙",
    "⻳": "龟",
}


def _plain(char: str) -> str:
    code = ord(char)
    if 0x2F00 <= code <= 0x2FDF or 0xF900 <= code <= 0xFAFF:
        return unicodedata.normalize("NFKC", char)
    if 0x2E80 <= code <= 0x2EFF:
        return _SUPPLEMENT.get(char, char)
    return char


def plain_cjk(text: str) -> str:
    """Replace radical and compatibility code points with regular CJK ideographs.

    Example: ⽰例市第⼀⼈⺠医院 → 示例市第一人民医院."""
    if not any(0x2E80 <= ord(c) <= 0x2FDF or 0xF900 <= ord(c) <= 0xFAFF for c in text):
        return text
    return "".join(_plain(c) for c in text)
