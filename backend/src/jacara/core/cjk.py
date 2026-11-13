"""PDF 文字层里"长得一样、编码不一样"的汉字，换成平常的写法。

有的 PDF（浏览器、Word 用某些中文字体导出的）把一部分汉字记成部首的编码：
"示"记成 ⽰（U+2F70，康熙部首），"小"记成 ⼩，"门"记成 ⻔（U+2ED4，中日韩部首补充），
"民"记成 ⺠。屏幕上看一模一样，复制出来却是另一个字。
模型读的时候会当成平常的字（王小明），系统拿它去原文核对就对不上，值被当成"原文里对不上"不填。
所以读 PDF 文字层时先换过来。

- 康熙部首（U+2F00–U+2FD5）、兼容汉字（U+F900–U+FAFF）：Unicode 标准的 NFKC 就有对应的字；
- 部首补充（U+2E80–U+2EF3）：标准里大多没有对应，下面的表只收能单独成字、字形就是那个字的。
  偏旁（亻、氵、扌这类的变体）不换。
只换这几段里的字，别的（全角冒号、括号）保持原样。
"""

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
    """把部首编码、兼容编码的汉字换成平常的汉字（⽰例市第⼀⼈⺠医院 → 示例市第一人民医院）。"""
    if not any(0x2E80 <= ord(c) <= 0x2FDF or 0xF900 <= ord(c) <= 0xFAFF for c in text):
        return text
    return "".join(_plain(c) for c in text)
