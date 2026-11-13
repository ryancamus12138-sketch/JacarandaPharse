"""在原文里找一个数（core/numbers.py）：原文里的数读出来按数值比，不再猜写法。"""

import pytest

from jacara.core.numbers import number_pattern, numbers_in, readings


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (78.5, "NEUT% 78.5 ↑"),  # 化验单的一位小数（以前只找 78.50）
        (78.5, "78.50"),  # 写了两位小数
        (0.9, "EO% 0.9"),
        (1.234, "单价 1.234"),  # 三位小数（以前只找 1.23）
        (0.123456789, "0.123456789"),  # 位数再多也行
        (1234.5, "RM 1,234.50"),  # 千分位
        (1234.56, "1.234,56 €"),  # 欧洲写法：句号千分位、逗号小数点
        (12.5, "12,5 %"),  # 欧洲的小数逗号
        (1234567.0, "1,234,567.00"),
        (6.0, "SUMMERINA 6.00-"),  # 折扣：不管正负号
        (-6.0, "(6.00)"),
        (9.0, "Total :  9.00"),
        (9.0, "9.0"),
        (9.0, "9.00,"),  # 句尾的逗号不算
    ],
)
def test_found(value, text):
    assert number_pattern(value, loose=False).search(text)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (9.0, "19.00"),  # 只认完整的数，不从中间截
        (9.0, "9.000"),  # 9 不认 9.000（常常是重量、数量）
        (0.9, "0.95"),
        (78.5, "178.5"),
        (55.0, "NO.55"),  # 前面紧挨着句号：不是一个独立的数
        (25.0, "25.12.2018"),  # 日期不是数
        (7.0, "007"),  # 前导 0：编号
        (9.0, "9"),  # 写成整数的只在 loose 时认
    ],
)
def test_not_found(value, text):
    assert number_pattern(value, loose=False).search(text) is None


def test_whole_numbers_only_when_loose():
    assert number_pattern(9.0, loose=True).search("Qty 9")
    assert number_pattern(1234.0, loose=True).search("1,234")
    assert number_pattern(1234.0, loose=True).search("1.234")  # 欧洲的千分位
    assert not number_pattern(9.5, loose=True).search("9")


def test_span_points_at_the_number():
    found = number_pattern(78.5, loose=False).search("中性粒细胞 78.5 ↑")
    assert found and (found.start(), found.end(), found.group()) == (6, 10, "78.5")


def test_unclear_separators_keep_both_readings():
    assert {str(r.value) for r in readings("1,234")} == {"1.234", "1234"}
    assert {str(r.value) for r in readings("12,50")} == {"12.50"}
    assert readings("1.234.56,7") == ()
    assert [t.text for t in numbers_in("3.5-9.5 10^9/L")] == ["3.5", "9.5", "10", "9"]


def test_float_noise_does_not_matter():
    assert number_pattern(0.1 + 0.2, loose=False).search("0.30000000000000004")
    assert number_pattern(0.3, loose=False).search("0.30")
