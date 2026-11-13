"""在样例上框选来定义模板（ADR 0047）：框里是什么值、旁边的标签是什么、在页面哪里、像什么类型。

只用这一页存下来的 OCR 结果（坐标和页面图片一样）；框里没有认出的字时，调用方再局部重新识别。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from jacara.core.daterange import parse_date_range
from jacara.core.textvalue import parse_date, parse_number

Box = Sequence[float]

_NUMBER_ONLY = re.compile(
    r"(?:[A-Za-z]{2,3}\.?\s+)?[-+(]?\s*[$€£¥₹]?\s*\d+(?:[,\s]\d{3})*(?:\.\d+)?\)?"
    r"\s*(?:%|[A-Za-z]{1,4}\.?)?"
)
"""整段就是一个数（可带货币、单位）：1,234.50、$4.11、RM 9.00；INV-0042、2018-12-25 不算。"""


def _cover(line: Box, box: Box) -> float:
    """文字块有多少落在框里（占文字块的比例）。"""
    w = min(line[2], box[2]) - max(line[0], box[0])
    h = min(line[3], box[3]) - max(line[1], box[1])
    area = (line[2] - line[0]) * (line[3] - line[1])
    return (w * h) / area if w > 0 and h > 0 and area > 0 else 0.0


def lines_in(lines: Sequence[dict[str, Any]], box: Box) -> list[dict[str, Any]]:
    """一半以上落在框里的文字块，按阅读顺序。"""
    inside = [ln for ln in lines if _cover(ln["box"], box) >= 0.5]
    return sorted(inside, key=lambda ln: (round(ln["box"][1] / 10), ln["box"][0]))


def pair_for(pairs: Sequence[Any], box: Box) -> Any | None:
    """框住的是版面分析配好的哪一对"标题 → 值"（ADR 0034）：值的框大部分在框里、框也大部分是这个值。
    对不上返回 None（不猜）。pairs 是 StructurePair（有 key、value_box）。"""
    best: tuple[float, Any] | None = None
    for pair in pairs:
        value = pair.value_box
        w = min(value[2], box[2]) - max(value[0], box[0])
        h = min(value[3], box[3]) - max(value[1], box[1])
        if w <= 0 or h <= 0:
            continue
        inter = w * h
        of_value = inter / max(1.0, (value[2] - value[0]) * (value[3] - value[1]))
        of_box = inter / max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        if of_value >= 0.6 and of_box >= 0.4 and (best is None or of_value + of_box > best[0]):
            best = (of_value + of_box, pair)
    return None if best is None else best[1]


def _bare(text: str) -> str:
    return re.sub(r"[\s:：*#]+$", "", str(text)).strip()


def guess_type(text: str) -> str:
    """像数字就是 number，像日期范围是 date_range，像日期是 date，其他 string。"""
    text = text.strip()
    if not text:
        return "string"
    digits = re.sub(r"\D", "", text)
    code_like = re.fullmatch(r"\d+", text) is not None and (
        text.startswith("0") or len(digits) >= 9
    )
    if code_like:
        return "string"  # 账号、编号（00030340067212、296664039561）：当数字会丢掉前面的 0
    if _NUMBER_ONLY.fullmatch(text) and parse_number(text) is not None and not parse_date(text):
        return "number"
    found = parse_date_range(text).range
    # 要有明显的"到"（前后带空格的横线、~、至、to），2018-12-25 里的横线不算
    joined = re.search(r"\s[-–—]\s|[~～至到]|\bto\b|\bthrough\b", text, re.IGNORECASE)
    if found is not None and found.start != found.end and joined:
        return "date_range"
    if parse_date(text) is not None:
        return "date"
    return "string"


def position(box: Box, width: float, height: float) -> str:
    """框在页面的哪里：左上、上方中间、右上……（页面分成 3 × 3 格）。"""
    cx = (box[0] + box[2]) / 2 / max(width, 1)
    cy = (box[1] + box[3]) / 2 / max(height, 1)
    row = "上" if cy < 1 / 3 else "中" if cy < 2 / 3 else "下"
    col = "左" if cx < 1 / 3 else "中" if cx < 2 / 3 else "右"
    if row == "中" and col == "中":
        return "中间"
    if col == "中":
        return {"上": "上方中间", "下": "下方中间"}[row]
    if row == "中":
        return {"左": "左边中间", "右": "右边中间"}[col]
    return f"{col}{row}角"


def field_id(label: str, used: set[str], n: int) -> str:
    """从标签猜字段 ID：英文就用它（Invoice No. → invoice_no），中文编号 field_3。"""
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", base or ""):
        base = f"field_{n}"
    candidate = base
    k = 2
    while candidate in used:
        candidate = f"{base}_{k}"
        k += 1
    used.add(candidate)
    return candidate


def hint(where: str, label: str | None, side: str | None) -> str:
    """给模型看的位置说明：在单据右上角，「Invoice No」右边。"""
    text = f"在单据{where}"
    if label:
        text += f"，「{label}」{side}"
    return text
