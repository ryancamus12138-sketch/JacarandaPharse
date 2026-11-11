"""模板：一种单据要抽哪些字段、检查哪些规则。每个模板是这个文件夹里的一个 .toml 文件。

为什么用文件而不是写在代码里：改字段、改说明、加一种新单据，不用改 Python 代码；
每个模板有版本号，每份文档都记下"按哪个模板的哪一版抽的"（项目规则 4：每次抽取都记录来源）。

用 TOML 格式：Python 自带 tomllib，不用装新依赖；比 JSON 好写，能写注释。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, get_args

from jacara.plugins.extractors.base import FieldSpec, FieldType
from jacara.plugins.validators.base import Rule
from jacara.plugins.validators.rules import RULES

_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
"""字段名：小写英文字母开头，只用小写字母、数字、下划线（存进数据库、导出、接口里都用它）。"""

DEFAULT_TEMPLATE = "receipt"
"""上传的文件默认用的模板。"""


class TemplateError(ValueError):
    """找不到模板，或者模板文件写得不对。说明直接给人看。"""


@dataclass(frozen=True)
class TemplateField:
    name: str
    label: str
    """给人看的名字，例如"总额"。"""
    type: FieldType
    required: bool
    description: str
    """给大模型看的说明。"""

    @property
    def spec(self) -> FieldSpec:
        """抽取器认识的字段格式。"""
        return FieldSpec(self.name, self.type, self.description)


@dataclass(frozen=True)
class Template:
    name: str
    version: int
    title: str
    description: str
    fields: tuple[TemplateField, ...]
    rules: tuple[Rule, ...]

    @property
    def specs(self) -> tuple[FieldSpec, ...]:
        return tuple(f.spec for f in self.fields)


def template_names() -> list[str]:
    """所有模板的名字，按字母排序。"""
    return sorted(
        p.name.removesuffix(".toml")
        for p in files(__package__).iterdir()
        if p.name.endswith(".toml")
    )


@cache
def load_template(name: str) -> Template:
    """读一个模板。读一次之后一直用同一份；改了文件要重启程序才生效。"""
    path = files(__package__).joinpath(f"{name}.toml")
    if not path.is_file():
        raise TemplateError(f"没有叫 {name} 的模板，现有：{'、'.join(template_names())}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise TemplateError(f"模板 {name}.toml 格式不对：{err}") from err
    return parse_template(raw, name)


def parse_template(raw: dict[str, Any], name: str) -> Template:
    """把 TOML 读出来的字典检查一遍，变成 Template。写错的地方说清楚是哪一项。"""
    where = f"模板 {name}"
    if raw.get("name") != name:
        raise TemplateError(f"{where}：文件里的 name 应该是 {name}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise TemplateError(f"{where}：version 应该是大于 0 的整数")

    fields: list[TemplateField] = []
    for i, item in enumerate(raw.get("fields", []), start=1):
        field_name = item.get("name")
        if not isinstance(field_name, str) or not _FIELD_NAME.match(field_name):
            raise TemplateError(f"{where}：第 {i} 个字段的 name 只能用小写英文字母、数字和下划线")
        if item.get("type") not in get_args(FieldType):
            raise TemplateError(f"{where}：字段 {field_name} 的 type 只能是 string、number、date")
        if any(f.name == field_name for f in fields):
            raise TemplateError(f"{where}：字段 {field_name} 重复了")
        fields.append(
            TemplateField(
                name=field_name,
                label=str(item.get("label") or field_name),
                type=item["type"],
                required=bool(item.get("required", False)),
                description=str(item.get("description", "")),
            )
        )
    if not fields:
        raise TemplateError(f"{where}：至少要有一个字段")

    rules: list[Rule] = []
    for rule_id in raw.get("rules", []):
        if rule_id not in RULES:
            raise TemplateError(f"{where}：没有编号为 {rule_id} 的规则，现有：{'、'.join(RULES)}")
        rules.append(RULES[rule_id])

    return Template(
        name=name,
        version=version,
        title=str(raw.get("title") or name),
        description=str(raw.get("description", "")),
        fields=tuple(fields),
        rules=tuple(rules),
    )
