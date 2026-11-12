"""模板：一种单据要抽哪些字段、检查哪些规则。

三种来源，同一种格式（ADR 0030）：
- 这个文件夹里的 .toml：评测和命令行用的内置模板（receipt、receipt_full），网页上传不再用它们；
- starters/ 里的 .toml：起步模板库，网页上"添加到我的模板"就是复制一份进数据库；
- 数据库里的"我的模板"：用户新建、编辑，每次保存一个新版本，定义是和 .toml 一样结构的 JSON。

为什么用文件而不是写在代码里：改字段、改说明、加一种新单据，不用改 Python 代码；
每个模板有版本号，每份文档都记下"按哪个模板的哪一版抽的"（项目规则 4：每次抽取都记录来源）。

用 TOML 格式：Python 自带 tomllib，不用装新依赖；比 JSON 好写，能写注释。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from functools import cache
from importlib.resources import files
from typing import Any, get_args

from jacara.formulas import (
    Formula,
    FormulaError,
    Inputs,
    Scope,
    check_condition,
    order,
    parse_condition,
    parse_formula,
)
from jacara.plugins.extractors.base import FieldSpec, FieldType, TableSpec
from jacara.plugins.validators.base import Rule, Severity
from jacara.plugins.validators.rules import REGIONS, RULES, RuleValidator
from jacara.templates.extras import (
    ExtrasError,
    TemplateEntity,
    TemplateReference,
    Workbook,
    entity_dict,
    parse_entities,
    parse_references,
    parse_workbook,
    reference_dict,
    workbook_dict,
)

_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
"""字段名：小写英文字母开头，只用小写字母、数字、下划线（存进数据库、导出、接口里都用它）。"""

REGION_NAMES = {"": "通用", "cn": "中国", "au": "澳洲"}
"""模板的地区：通用模板只用通用规则；中国、澳洲的模板可以再用本地区的规则（ADR 0035）。"""

DEFAULT_TEMPLATE = "receipt"
"""上传的文件默认用的模板。"""


class TemplateError(ValueError):
    """找不到模板，或者模板文件写得不对。说明直接给人看。"""


@dataclass(frozen=True)
class TemplateField:
    name: str
    """字段 ID：永远不变（显示名可以改）。存进数据库、规则、导出对齐都靠它。"""
    label: str
    """给人看的名字，例如"总额"。也是导出 Excel 时的列名。"""
    type: FieldType
    required: bool
    description: str
    """给大模型看的说明。"""
    aliases: tuple[str, ...] = ()
    """单据上的其他写法（Check-in、Arrival、入住日期），一起告诉模型。"""
    export: bool = True
    """导出 Excel 时要不要这一列。"""
    sensitive: bool = False
    """敏感信息（证件号码等）：默认不导出。"""
    options: tuple[str, ...] = ()
    """可选值（来自 Excel 的下拉选项等）：抽到的值不在里面就提醒。空 = 不限。"""
    extract: bool = True
    """要不要让模型从单据上抽。False = 人工填（报销人、部门……）：不交给模型，审核时填。"""
    unique: bool = False
    """不能重复（ADR 0036）：发票号码、账单号这类。别的文档里有一样的值，就提醒"可能重复报销"。"""
    compute: str = ""
    """计算字段的公式（ADR 0035），例如 check_out - check_in。
    有公式就不交给模型，由别的字段算出来。"""

    @property
    def asks_model(self) -> bool:
        """交给模型从单据上抽：不是人工填的，也不是算出来的。"""
        return self.extract and not self.compute

    @property
    def spec(self) -> FieldSpec:
        """抽取器认识的字段格式。别名、可选值写进说明里，模型才知道还有哪些写法、只能填什么。"""
        description = self.description
        if self.aliases:
            description = f"{description}（单据上可能写作：{'、'.join(self.aliases)}）".lstrip()
        if self.options:
            description = f"{description}（只能是以下之一：{'、'.join(self.options)}）".lstrip()
        return FieldSpec(self.name, self.type, description)


@dataclass(frozen=True)
class TemplateTable:
    """一张表（例如商品明细）：每行有同样的几列，行数由单据决定。"""

    name: str
    label: str
    description: str
    columns: tuple[TemplateField, ...]
    required: bool = False
    """至少要有一行。"""
    export: bool = True

    @property
    def spec(self) -> TableSpec:
        columns = tuple(c.spec for c in self.columns if c.asks_model)
        return TableSpec(self.name, self.description, columns)


@dataclass(frozen=True)
class CustomRule:
    """自己写的规则（ADR 0035）：一个条件，例如 sum(items.amount) == total。

    per_row 写表名时，每一行单独检查，条件里写这张表的列名（quantity * unit_price == amount）。
    """

    title: str
    check: str
    severity: Severity = "error"
    per_row: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "check": self.check,
            "severity": self.severity,
            "per_row": self.per_row,
        }


@dataclass(frozen=True)
class Formulas:
    """一个模板的计算字段，已经按依赖排好顺序（先算别人要用的）。"""

    fields: dict[str, Formula]
    """普通字段的公式。"""
    columns: dict[str, dict[str, Formula]]
    """表名 → {列名 → 每一行的公式}。"""

    def __bool__(self) -> bool:
        return bool(self.fields or self.columns)


@dataclass(frozen=True)
class Template:
    name: str
    version: int
    title: str
    description: str
    fields: tuple[TemplateField, ...]
    rules: tuple[Rule, ...]
    tables: tuple[TemplateTable, ...] = ()
    category: str = ""
    """类型标签：发票、交通、收据、住宿……上传时分组显示用。"""
    enforce_required: bool = False
    """必填字段没值算"错误"。起步模板和我的模板打开；内置模板（评测、命令行）不打开，
    结果和以前一样。"""
    entities: tuple[TemplateEntity, ...] = ()
    """对象（ADR 0031）：几份单据说的同一个人、同一家公司，导出时按编号合并成一行。"""
    references: tuple[TemplateReference, ...] = ()
    """参照表：固定名单，用来对照、补编号。"""
    workbook: Workbook | None = None
    """按原 Excel 导出的样式。没有就用标准的导出格式。"""
    custom_rules: tuple[CustomRule, ...] = ()
    """自己写的规则（ADR 0035），和选的内置规则一起检查。"""
    region: str = ""
    """地区：空 = 通用；cn 中国、au 澳洲。只能选通用规则和本地区的规则。"""

    def region_problem(self) -> str | None:
        """选了别的地区的规则（例如通用收据选了澳洲 ATO 的规则）：说清楚是哪几条。"""
        wrong = [r.title for r in self.rules if REGIONS.get(r.id, self.region) != self.region]
        if not wrong:
            return None
        return (
            f"这是{REGION_NAMES[self.region]}模板，不能用别的地区的规则："
            f"{'、'.join(wrong)}。请关掉，需要的话自己写规则"
        )

    def all_rules(self) -> tuple[Rule, ...]:
        """选的内置规则 + 自己写的规则（编号 custom_1、custom_2……）。"""
        return self.rules + tuple(
            self._custom(i, c) for i, c in enumerate(self.custom_rules, start=1)
        )

    def _scope(self, per_row: str | None) -> Scope:
        fields = {f.name: f.type for f in self.fields}
        tables = {t.name: {c.name: c.type for c in t.columns} for t in self.tables}
        if per_row is None:
            return Scope(fields, tables)
        return Scope({}, tables, tables[per_row])

    def _custom(self, number: int, custom: CustomRule) -> Rule:
        condition = parse_condition(custom.check, self._scope(custom.per_row))
        labels = self.labels()
        if custom.per_row:
            labels = {
                **labels,
                **{
                    c.name: c.label
                    for t in self.tables
                    if t.name == custom.per_row
                    for c in t.columns
                },
            }
            needs = tuple(sorted(condition.columns))
        else:
            needs = tuple(sorted(condition.fields)) + tuple(
                f"{t}.*.*" for t in sorted(condition.tables)
            )
        readable = condition.readable(labels)
        left_text = condition.left.readable(labels)
        right_text = condition.right.readable(labels)
        per_row = custom.per_row

        def check(values: dict[str, Any]) -> str | None:
            inputs = Inputs({}, {}, values) if per_row else Inputs(values, _tables_of(values))
            verdict = check_condition(condition, inputs)
            if verdict.passed is not False:
                return None
            return (
                f"{left_text} 是 {_show(verdict.left)}，{right_text} 是 {_show(verdict.right)}，"
                f"不满足 {readable}"
            )

        return Rule(
            f"custom_{number}",
            custom.title or readable,
            needs,
            check,
            custom.severity,
            per_row=per_row,
        )

    @property
    def specs(self) -> tuple[FieldSpec, ...]:
        """交给模型抽的字段（人工填的不交）。"""
        return tuple(f.spec for f in self.fields if f.asks_model)

    @property
    def table_specs(self) -> tuple[TableSpec, ...]:
        return tuple(t.spec for t in self.tables if any(c.asks_model for c in t.columns))

    @property
    def manual_fields(self) -> tuple[TemplateField, ...]:
        """人工填的普通字段：处理时先放一个空值，审核时填。"""
        return tuple(f for f in self.fields if not f.extract and not f.compute)

    def formulas(self) -> Formulas:
        """检查过的公式（ADR 0035）。模板读进来时已经检查过，这里不会出错。"""
        fields = {f.name: f.type for f in self.fields}
        tables = {t.name: {c.name: c.type for c in t.columns} for t in self.tables}
        top = {
            f.name: parse_formula(f.compute, Scope(fields, tables))
            for f in self.fields
            if f.compute
        }
        rows = {
            t.name: {
                c.name: parse_formula(c.compute, Scope(fields, tables, tables[t.name]))
                for c in t.columns
                if c.compute
            }
            for t in self.tables
            if any(c.compute for c in t.columns)
        }
        return Formulas(
            fields={name: top[name] for name in order(top)},
            columns={t: {c: f[c] for c in order(f)} for t, f in rows.items()},
        )

    def validator(self) -> RuleValidator:
        """这个模板的校验器：模板选的规则 + 必填 + 可选值 + 参照表对照。"""
        options = [(f.name, f.label, f.options) for f in self.fields if f.options]
        for t in self.tables:
            options += [
                (f"{t.name}.*.{c.name}", c.label, c.options) for c in t.columns if c.options
            ]
        # 表格的列（items.lab_name）对照每一行：items.*.lab_name
        lookups = [
            (r.match_field.replace(".", ".*.", 1), r.label, r.values())
            for r in self.references
            if r.match_field
        ]
        if not self.enforce_required:
            return RuleValidator(self.all_rules(), options=options, lookups=lookups)
        required = [(f.name, f.label) for f in self.fields if f.required]
        required += [(t.name, t.label) for t in self.tables if t.required]
        return RuleValidator(self.all_rules(), required=required, options=options, lookups=lookups)

    def labels(self) -> dict[str, str]:
        """字段 ID → 中文名。表格写成 items（表名）和 items.amount（列）。"""
        labels = {f.name: f.label for f in self.fields}
        for t in self.tables:
            labels[t.name] = t.label
            labels.update({f"{t.name}.{c.name}": c.label for c in t.columns})
        return labels

    def definition(self) -> dict[str, Any]:
        """和 .toml 一样结构的字典：存进数据库（我的模板的一个版本）就是它。"""
        return {
            "fields": [_field_dict(f) for f in self.fields],
            "tables": [
                {
                    "name": t.name,
                    "label": t.label,
                    "description": t.description,
                    "required": t.required,
                    "export": t.export,
                    "columns": [_field_dict(c) for c in t.columns],
                }
                for t in self.tables
            ],
            "rules": [r.id for r in self.rules],
            "entities": [entity_dict(e) for e in self.entities],
            "references": [reference_dict(r) for r in self.references],
            "workbook": workbook_dict(self.workbook) if self.workbook else None,
            "custom_rules": [c.as_dict() for c in self.custom_rules],
            "region": self.region,
        }


def _tables_of(values: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """items.2.amount 这样的格子 → 表名 → 每一行 {列名 → 值}。"""
    from jacara.plugins.extractors.base import split_cell_name

    rows: dict[str, dict[int, dict[str, Any]]] = {}
    for name, value in values.items():
        cell = split_cell_name(name)
        if cell:
            rows.setdefault(cell[0], {}).setdefault(cell[1], {})[cell[2]] = value
    return {t: [r[i] for i in sorted(r)] for t, r in rows.items()}


def _show(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def _field_dict(f: TemplateField) -> dict[str, Any]:
    return {
        "name": f.name,
        "label": f.label,
        "type": f.type,
        "required": f.required,
        "description": f.description,
        "aliases": list(f.aliases),
        "export": f.export,
        "sensitive": f.sensitive,
        "options": list(f.options),
        "extract": f.extract,
        "compute": f.compute,
        "unique": f.unique,
    }


def template_names() -> list[str]:
    """内置模板（评测、命令行用）的名字，按字母排序。"""
    return sorted(
        p.name.removesuffix(".toml")
        for p in files(__package__).iterdir()
        if p.name.endswith(".toml")
    )


STARTERS = (
    "cn_einvoice",
    "cn_air_itinerary",
    "cn_rail_ticket",
    "intl_invoice",
    "au_tax_invoice",
    "receipt_general",
    "receipt_shopping",
    "receipt_restaurant",
    "hotel_folio",
)
"""起步模板库，按这个顺序显示（ADR 0030）。"""


@cache
def load_starter(name: str) -> Template:
    """读一个起步模板（starters/ 里的 .toml）。"""
    if name not in STARTERS:
        raise TemplateError(f"没有叫 {name} 的起步模板，现有：{'、'.join(STARTERS)}")
    path = files(__package__).joinpath("starters", f"{name}.toml")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise TemplateError(f"起步模板 {name}.toml 格式不对：{err}") from err
    return replace(parse_template(raw, name), enforce_required=True)


def from_definition(
    definition: dict[str, Any],
    name: str,
    version: int,
    title: str,
    category: str = "",
    description: str = "",
) -> Template:
    """数据库里"我的模板"的一个版本 → Template。定义的格式和 .toml 一样，检查也一样。"""
    raw = {
        **definition,
        "name": name,
        "version": version,
        "title": title,
        "category": category,
        "description": description,
    }
    return replace(parse_template(raw, name, check_name=False), enforce_required=True)


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


def parse_template(raw: dict[str, Any], name: str, *, check_name: bool = True) -> Template:
    """把 TOML 读出来的字典检查一遍，变成 Template。写错的地方说清楚是哪一项。"""
    where = f"模板 {name}" if check_name else f"模板「{raw.get('title') or name}」"
    if check_name and raw.get("name") != name:
        raise TemplateError(f"{where}：文件里的 name 应该是 {name}")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise TemplateError(f"{where}：version 应该是大于 0 的整数")

    fields = _fields(raw.get("fields", []), where)
    if not fields:
        raise TemplateError(f"{where}：至少要有一个字段")

    tables: list[TemplateTable] = []
    for i, item in enumerate(raw.get("tables", []), start=1):
        table_name = item.get("name")
        if not isinstance(table_name, str) or not _FIELD_NAME.match(table_name):
            raise TemplateError(f"{where}：第 {i} 张表的 name 只能用小写英文字母、数字和下划线")
        if any(f.name == table_name for f in fields) or any(t.name == table_name for t in tables):
            raise TemplateError(f"{where}：表 {table_name} 和别的字段或表重名了")
        columns = _fields(item.get("columns", []), f"{where} 的表 {table_name}")
        if not columns:
            raise TemplateError(f"{where}：表 {table_name} 至少要有一列")
        if any(c.name == "row" for c in columns):
            raise TemplateError(f"{where}：表 {table_name} 的列不能叫 row（留给行号用）")
        tables.append(
            TemplateTable(
                name=table_name,
                label=str(item.get("label") or table_name),
                description=str(item.get("description", "")),
                columns=tuple(columns),
                required=bool(item.get("required", False)),
                export=bool(item.get("export", True)),
            )
        )

    rules: list[Rule] = []
    for rule_id in raw.get("rules", []):
        if rule_id not in RULES:
            raise TemplateError(f"{where}：没有编号为 {rule_id} 的规则，现有：{'、'.join(RULES)}")
        rules.append(RULES[rule_id])

    field_ids = {f.name for f in fields}
    refs = field_ids | {f"{t.name}.{c.name}" for t in tables for c in t.columns}
    try:
        entities = parse_entities(raw.get("entities"), field_ids, where)
        references = parse_references(raw.get("references"), refs, where)
        workbook = parse_workbook(
            raw.get("workbook"),
            refs,
            {t.name for t in tables},
            {e.name for e in entities},
            {r.name: r for r in references},
            where,
        )
    except ExtrasError as err:
        raise TemplateError(str(err)) from err

    region = raw.get("region") or ""
    if region not in REGION_NAMES:
        raise TemplateError(f"{where}：region 只能是空的（通用）、cn 或 au")
    template = Template(
        name=name,
        version=version,
        region=region,
        title=str(raw.get("title") or name),
        description=str(raw.get("description", "")),
        fields=tuple(fields),
        rules=tuple(rules),
        tables=tuple(tables),
        category=str(raw.get("category", "")),
        entities=entities,
        references=references,
        workbook=workbook,
        custom_rules=_custom_rules(raw.get("custom_rules"), tables, where),
    )
    _check_formulas(template, where)
    for i, custom in enumerate(template.custom_rules, start=1):
        try:
            parse_condition(custom.check, template._scope(custom.per_row))
        except FormulaError as err:
            name = custom.title or f"第 {i} 条"
            raise TemplateError(f"{where}：自定义规则「{name}」：{err}") from err
    return template


def _custom_rules(items: Any, tables: list[TemplateTable], where: str) -> tuple[CustomRule, ...]:
    if items is None:
        return ()
    if not isinstance(items, list):
        raise TemplateError(f"{where}：custom_rules 应该是一个列表")
    rules: list[CustomRule] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise TemplateError(f"{where}：第 {i} 条自定义规则写得不对")
        check = item.get("check")
        if not isinstance(check, str) or not check.strip():
            raise TemplateError(f"{where}：第 {i} 条自定义规则没写条件")
        severity = item.get("severity", "error")
        if severity not in ("error", "warning"):
            raise TemplateError(f"{where}：第 {i} 条自定义规则的 severity 只能是 error 或 warning")
        per_row = item.get("per_row") or None
        if per_row is not None and per_row not in {t.name for t in tables}:
            raise TemplateError(f"{where}：第 {i} 条自定义规则的 per_row 不是这个模板里的表")
        rules.append(
            CustomRule(str(item.get("title") or "").strip(), check.strip(), severity, per_row)
        )
    return tuple(rules)


def _check_formulas(template: Template, where: str) -> None:
    """逐条检查公式，写错了说清楚是哪个字段。"""
    fields = {f.name: f.type for f in template.fields}
    tables = {t.name: {c.name: c.type for c in t.columns} for t in template.tables}
    places = [(f, Scope(fields, tables), f.label) for f in template.fields]
    places += [
        (c, Scope(fields, tables, tables[t.name]), f"{t.label}的{c.label}")
        for t in template.tables
        for c in t.columns
    ]
    for field, scope, label in places:
        if not field.compute:
            continue
        try:
            formula = parse_formula(field.compute, scope)
        except FormulaError as err:
            raise TemplateError(f"{where}：「{label}」的公式：{err}") from err
        if scope.row is not None and formula.fields & {
            f.name for f in template.fields if f.compute
        }:
            raise TemplateError(f"{where}：「{label}」的公式不能用别的计算字段（只能用单据上抽的）")
    try:
        template.formulas()
    except FormulaError as err:
        raise TemplateError(f"{where}：{err}") from err


def _fields(items: list[dict[str, Any]], where: str) -> list[TemplateField]:
    """检查一组字段（模板的字段，或者一张表的列）。"""
    fields: list[TemplateField] = []
    for i, item in enumerate(items, start=1):
        field_name = item.get("name")
        if not isinstance(field_name, str) or not _FIELD_NAME.match(field_name):
            raise TemplateError(f"{where}：第 {i} 个字段的 name 只能用小写英文字母、数字和下划线")
        if item.get("type") not in get_args(FieldType):
            raise TemplateError(f"{where}：字段 {field_name} 的 type 只能是 string、number、date")
        if any(f.name == field_name for f in fields):
            raise TemplateError(f"{where}：字段 {field_name} 重复了")
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise TemplateError(f"{where}：字段 {field_name} 的 aliases 应该是文字列表")
        sensitive = bool(item.get("sensitive", False))
        options = item.get("options", [])
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise TemplateError(f"{where}：字段 {field_name} 的 options 应该是文字列表")
        compute = item.get("compute", "")
        if not isinstance(compute, str):
            raise TemplateError(f"{where}：字段 {field_name} 的 compute 应该是一条公式")
        if compute.strip() and item["type"] != "number":
            raise TemplateError(f"{where}：字段 {field_name} 有公式，type 要是 number")
        fields.append(
            TemplateField(
                name=field_name,
                label=str(item.get("label") or field_name),
                type=item["type"],
                required=bool(item.get("required", False)),
                description=str(item.get("description", "")),
                aliases=tuple(a.strip() for a in aliases if a.strip()),
                # 敏感字段默认不导出；明确写了 export 就按写的
                export=bool(item.get("export", not sensitive)),
                sensitive=sensitive,
                options=tuple(dict.fromkeys(o.strip() for o in options if o.strip())),
                extract=bool(item.get("extract", True)),
                compute=compute.strip(),
                unique=bool(item.get("unique", False)),
            )
        )
    return fields
