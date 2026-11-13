"""规则校验扩展点：用单据上的数字和常识互相验证。

证据对齐只能回答"值在不在原文里"。如果 OCR 把原文读错了（1.00 读成 100），
对齐会把错值当成有出处。规则校验换个角度：现金 − 总额 应该等于 找零，对不上就说明有值错了，
不管 OCR 置信度有多高。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from jacara.plugins.extractors.base import ExtractedField

Severity = Literal["error", "warning"]
"""error：肯定有值错了。warning：可疑，但也可能没错（例如日期在未来，可能是电脑时间不对）。"""

Values = dict[str, str | float | None]
"""字段名 → 值。规则只看值，不关心它是怎么抽出来的。"""


@dataclass(frozen=True)
class Rule:
    """一条规则：名字、要用到哪些字段、怎么检查。

    check 返回 None 表示通过，返回一句话表示不通过（这句话直接给人看）。
    needs 里的字段只要有一个没值，这条规则就跳过，不算错。

    表格（商品明细）的两种规则：
    - needs 里写 items.*.amount：表示"表里所有行的金额"，至少有一行有值才检查。
    - per_row="items"：每一行单独检查一次，needs 写列名（quantity、amount），
      check 拿到的是这一行的值。
    flags：不通过时标黄哪些字段。不写就标 needs 里的全部。
    """

    id: str
    title: str
    needs: tuple[str, ...]
    check: Callable[[Values], str | None]
    severity: Severity = "error"
    per_row: str | None = None
    flags: tuple[str, ...] | None = None
    requires: tuple[str, ...] = ()
    """模板里要有这些字段（值可以是空的）这条规则才算"用得上"。查"缺了什么"的规则靠它，
    只出现在相关的模板里（收银小票的 TSE 规则不会出现在德国发票模板里，ADR 0049）。"""


@dataclass(frozen=True)
class Issue:
    """一条没通过的规则。fields 是牵涉到的字段：不知道是哪个错了，所以全部标出。"""

    rule: str
    title: str
    message: str
    fields: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True)
class ValidationResult:
    """校验结果：没通过的、通过的、因为缺字段跳过的。"""

    issues: tuple[Issue, ...]
    passed: tuple[str, ...]
    skipped: tuple[str, ...]

    def fields_with_issues(self) -> set[str]:
        """所有牵涉到问题的字段名。"""
        return {name for issue in self.issues for name in issue.fields}


class Validator(Protocol):
    """校验器接口：任何类只要有 name 属性和 validate 方法，就是一个校验器。"""

    name: str

    def validate(self, fields: Sequence[ExtractedField]) -> ValidationResult:
        """检查一组字段，返回问题清单。"""
        ...
