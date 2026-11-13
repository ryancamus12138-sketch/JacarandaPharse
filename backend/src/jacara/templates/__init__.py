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
from typing import Any, Literal, get_args

from jacara.core.daterange import ORDER_PROBLEM, Parsed, day_first_for, parse_date_range
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

COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")
"""国家和地区代码：ISO 3166-1 的两个大写字母（CN、AU、US……）。"""

_OLD_REGIONS = {"": (), "cn": ("CN",), "au": ("AU",)}
"""ADR 0035 时的写法（region = "cn"），读旧数据用。"""


def infer_countries(rule_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """没写国家的模板（加国家之前存的、从 Excel 或旧的模板文件导入的）：看它选的规则。
    选了哪几个国家的规则，就是哪几个国家；一条都没选就是通用（空）。"""
    return tuple(sorted({REGIONS[r] for r in rule_ids if r in REGIONS}))


def clean_labels(values: object, where: str, what: str, limit: int = 20) -> tuple[str, ...]:
    """一组标签：文字列表，去掉首尾空格、空的和重复的，每个最多 20 个字。"""
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
        raise TemplateError(f"{where}：{what}应该是文字列表")
    cleaned = tuple(dict.fromkeys(v.strip()[:20] for v in values if v.strip()))
    if len(cleaned) > limit:
        raise TemplateError(f"{where}：{what}最多 {limit} 个")
    return cleaned


def clean_countries(values: object, where: str) -> tuple[str, ...]:
    """国家和地区代码：大写，检查格式。空 = 通用。"""
    codes = tuple(c.upper() for c in clean_labels(values, where, "国家和地区", 300))
    wrong = [c for c in codes if not COUNTRY_CODE.match(c)]
    if wrong:
        raise TemplateError(
            f"{where}：国家和地区要写两个字母的代码（如 CN、AU），不认识：{'、'.join(wrong)}"
        )
    return codes


DEFAULT_TEMPLATE = "receipt"
"""上传的文件默认用的模板。"""


class TemplateError(ValueError):
    """找不到模板，或者模板文件写得不对。说明直接给人看。"""


RangeExport = Literal["normalized", "original", "columns"]
"""日期范围导出成：统一格式、原文、拆成两列（ADR 0039）。"""

LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3}|-[A-Z]{2})?$")
"""语言代码（BCP 47 的简写，ADR 0048）：zh、en、ja、ko、de、zh-Hant……"""

DEFAULT_LANGUAGE = "zh"
"""label 默认是哪种语言：起步模板都写中文。"""

Names = tuple[tuple[str, str], ...]
"""别的语言的名字（ADR 0048）：((语言代码, 名字), ...)。label 本身是默认语言。"""


PROMPT_LANGUAGES: dict[str, str] = {
    "CN": "zh", "TW": "zh-Hant", "HK": "zh-Hant", "JP": "ja", "KR": "ko", "MY": "ms",
    "VN": "vi", "IN": "hi", "TH": "th", "ID": "id", "DE": "de", "AT": "de", "FR": "fr",
    "IT": "it", "ES": "es", "NL": "nl", "PL": "pl", "CA": "fr", "AE": "ar", "SA": "ar",
}  # fmt: skip
"""国家 → 单据上常见的当地语言。给模型的提示词里只放英文和这些语言的名字（ADR 0049）："""


def prompt_languages(countries: tuple[str, ...]) -> frozenset[str]:
    """通用模板只给英文名；国家模板再加当地语言。界面切换语言不受影响。"""
    return frozenset({"en", *(PROMPT_LANGUAGES[c] for c in countries if c in PROMPT_LANGUAGES)})


def name_in(label: str, names: Names, language: str | None) -> str:
    """这种语言的名字；没指定语言、或者没写这种语言，用 label。"""
    if not language:
        return label
    return dict(names).get(language, label)


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
    range_export: RangeExport = "normalized"
    """日期范围（ADR 0039）导出成什么样：normalized 统一格式（2014-07-01 ~ 2014-07-31）、
    original 原文、columns 拆成开始、结束两列。别的类型用不到。"""
    range_separator: str = " ~ "
    """统一格式里开始、结束之间写什么。"""
    names: Names = ()
    """别的语言的名字（英文、当地语言，ADR 0048）：审核页、导出可以切换。
    也告诉模型单据上可能这么写。"""

    def label_in(self, language: str | None) -> str:
        return name_in(self.label, self.names, language)

    @property
    def asks_model(self) -> bool:
        """交给模型从单据上抽：不是人工填的，也不是算出来的。"""
        return self.extract and not self.compute

    @property
    def spec(self) -> FieldSpec:
        return self.spec_in(None)

    def spec_in(self, languages: frozenset[str] | None) -> FieldSpec:
        """抽取器认识的字段格式。别名、可选值写进说明里，模型才知道还有哪些写法、只能填什么。
        languages：别的语言的名字只放这几种（None = 全部）。"""
        description = self.description
        others = (n for lang, n in self.names if languages is None or lang in languages)
        written = [w for w in dict.fromkeys((*self.aliases, *others)) if w != self.label]
        if written:
            description = f"{description}（单据上可能写作：{'、'.join(written)}）".lstrip()
        if self.options:
            description = f"{description}（只能是以下之一：{'、'.join(self.options)}）".lstrip()
        if self.type == "date_range":
            description = f"{description}（日期范围：照抄原文，不要改格式、不要补年份）".lstrip()
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
    names: Names = ()
    """别的语言的表名（ADR 0048）。"""

    def label_in(self, language: str | None) -> str:
        return name_in(self.label, self.names, language)

    @property
    def spec(self) -> TableSpec:
        return self.spec_in(None)

    def spec_in(self, languages: frozenset[str] | None) -> TableSpec:
        columns = tuple(c.spec_in(languages) for c in self.columns if c.asks_model)
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
    countries: tuple[str, ...] = ()
    """国家和地区（ADR 0037）：空 = 通用。我的模板以数据库里 templates 表记的为准。"""
    domains: tuple[str, ...] = ()
    """功能领域（发票、交通、收据、住宿……），可以有几个。"""
    titles: Names = ()
    """模板名的别的语言写法（ADR 0048）。"""
    basis: tuple[str, ...] = ()
    """起步模板的依据：法规条款、使用场景、查证来源（模板文件开头的注释），预览时给人看。"""

    @property
    def languages(self) -> tuple[str, ...]:
        """能切换的语言：默认语言（中文）在前，再按模板里写的顺序。"""
        groups: list[Names] = [self.titles, *(f.names for f in self.fields)]
        for t in self.tables:
            groups += [t.names, *(c.names for c in t.columns)]
        found = dict.fromkeys([DEFAULT_LANGUAGE])
        for names in groups:
            found.update(dict.fromkeys(lang for lang, _ in names))
        return tuple(found)

    def title_in(self, language: str | None) -> str:
        return name_in(self.title, self.titles, language)

    @property
    def day_first(self) -> bool | None:
        """数字日期是不是 日/月/年（按国家，ADR 0039）：拆日期范围时用。不知道是 None。"""
        return day_first_for(self.countries)

    def ranges(self) -> dict[str, TemplateField]:
        """日期范围字段（ADR 0039）：字段 ID → 字段；表格的列写成 items.period。"""
        found = {f.name: f for f in self.fields if f.type == "date_range"}
        for t in self.tables:
            found |= {f"{t.name}.{c.name}": c for c in t.columns if c.type == "date_range"}
        return found

    def foreign_rules(self) -> list[str]:
        """选了不属于这个模板国家的规则（起步模板库要求：国家模板只用通用规则和本国规则，
        通用模板只用通用规则）。我的模板不限制，这个只给起步模板检查用。"""
        return [
            r.title for r in self.rules if r.id in REGIONS and REGIONS[r.id] not in self.countries
        ]

    def all_rules(self) -> tuple[Rule, ...]:
        """选的内置规则 + 自己写的规则（编号 custom_1、custom_2……）+ 日期范围的检查（ADR 0039）。"""
        return (
            self.rules
            + tuple(self._custom(i, c) for i, c in enumerate(self.custom_rules, start=1))
            + self._range_rules()
        )

    def _range_rules(self) -> tuple[Rule, ...]:
        """每个日期范围字段两条检查：拆不开或日月分不清（警告）、结束早于开始（错误）。"""
        day_first = self.day_first
        rules: list[Rule] = []
        places = [(f, f.name, None, f.label) for f in self.fields]
        places += [
            (c, c.name, t.name, f"{t.label}的{c.label}") for t in self.tables for c in t.columns
        ]
        for field, name, per_row, label in places:
            if field.type != "date_range":
                continue

            def parsed(values: dict[str, Any], name: str = name) -> Parsed | None:
                raw = values.get(name)
                return parse_date_range(str(raw), day_first) if raw not in (None, "") else None

            def readable(values: dict[str, Any], label: str = label, parsed=parsed) -> str | None:
                found = parsed(values)
                if found is None or found.problem is None or found.problem == ORDER_PROBLEM:
                    return None
                return f"{label}：{found.problem}，请核对开始和结束日期"

            def ordered(values: dict[str, Any], label: str = label, parsed=parsed) -> str | None:
                found = parsed(values)
                if found is None or found.problem != ORDER_PROBLEM or found.range is None:
                    return None
                return f"{label}：结束 {found.range.end} 早于开始 {found.range.start}"

            rules.append(
                Rule(f"range_{name}", f"{label}拆得开", (name,), readable, "warning", per_row)
            )
            rules.append(
                Rule(
                    f"range_order_{name}",
                    f"{label}结束不早于开始",
                    (name,),
                    ordered,
                    "error",
                    per_row,
                )
            )
        return tuple(rules)

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
            inputs = (
                Inputs({}, {}, values, self.day_first)
                if per_row
                else Inputs(values, _tables_of(values), day_first=self.day_first)
            )
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
        languages = prompt_languages(self.countries)
        return tuple(f.spec_in(languages) for f in self.fields if f.asks_model)

    @property
    def table_specs(self) -> tuple[TableSpec, ...]:
        languages = prompt_languages(self.countries)
        return tuple(
            t.spec_in(languages) for t in self.tables if any(c.asks_model for c in t.columns)
        )

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

    def labels(self, language: str | None = None) -> dict[str, str]:
        """字段 ID → 显示名（默认中文；指定语言就用那种语言，没写的用中文，ADR 0048）。
        表格写成 items（表名）和 items.amount（列）。"""
        labels = {f.name: f.label_in(language) for f in self.fields}
        for t in self.tables:
            labels[t.name] = t.label_in(language)
            labels.update({f"{t.name}.{c.name}": c.label_in(language) for c in t.columns})
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
                    **({"names": dict(t.names)} if t.names else {}),
                }
                for t in self.tables
            ],
            "rules": [r.id for r in self.rules],
            "entities": [entity_dict(e) for e in self.entities],
            "references": [reference_dict(r) for r in self.references],
            "workbook": workbook_dict(self.workbook) if self.workbook else None,
            "custom_rules": [c.as_dict() for c in self.custom_rules],
            **({"titles": dict(self.titles)} if self.titles else {}),
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
        **({"names": dict(f.names)} if f.names else {}),
        **(
            {"range_export": f.range_export, "range_separator": f.range_separator}
            if f.type == "date_range"
            else {}
        ),
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
    "lab_report",
    "quality_report",
    "cn_quality_report",
    "packing_list",
    "delivery_note",
    "cn_express_waybill",
    "bill_of_lading",
    "air_waybill",
    # 各国、各地区法定单据（ADR 0049）
    "jp_qualified_invoice",
    "kr_tax_invoice",
    "tw_uniform_invoice",
    "sg_tax_invoice",
    "my_einvoice",
    "vn_einvoice",
    "in_gst_invoice",
    "nz_taxable_supply",
    "us_lab_report",
    "gb_vat_invoice",
    "de_rechnung",
    "de_kassenbeleg",
    "th_tax_invoice",
    "id_faktur_pajak",
    "ph_vat_invoice",
    "fr_facture",
    "it_fattura",
    "es_factura",
    "nl_factuur",
    "pl_faktura",
    "ca_gst_hst_invoice",
    "ae_tax_invoice",
    "sa_tax_invoice",
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
    text = path.read_text(encoding="utf-8")
    return replace(parse_template(raw, name), enforce_required=True, basis=header_notes(text))


def header_notes(text: str) -> tuple[str, ...]:
    """模板文件开头的注释 → 一条一条的说明。缩进的、或者上一行没写完（不以句号结尾）的接到上一条；
    "只写字段……"这句不要。"""
    notes: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        body = line[1:]
        content = body.strip()
        if not content or content.startswith("只写字段") or "不放任何真实" in content:
            continue
        content = re.sub(r"^起步模板：", "适用：", content)
        if notes and (body.startswith("   ") or not notes[-1].endswith(("。", "；", "：", "."))):
            last = notes[-1]
            space = (
                " " if last[-1:].isascii() and last[-1:].isalnum() and content[:1].isascii() else ""
            )
            notes[-1] = last + space + content
        else:
            notes.append(content)
    return tuple(notes)


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
                names=_names(item.get("names"), where, f"表 {table_name}"),
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
        # 原表的一列可以填日期范围的开始、结束、天数（ADR 0039）
        range_parts = {
            f"{f.name}.{part}"
            for f in fields
            if f.type == "date_range"
            for part in ("start", "end", "days")
        }
        workbook = parse_workbook(
            raw.get("workbook"),
            refs | range_parts,
            {t.name for t in tables},
            {e.name for e in entities},
            {r.name: r for r in references},
            where,
        )
    except ExtrasError as err:
        raise TemplateError(str(err)) from err

    if "countries" in raw:
        countries = clean_countries(raw.get("countries"), where)
    elif str(raw.get("region") or "") in _OLD_REGIONS:
        countries = (
            _OLD_REGIONS[str(raw.get("region") or "")]
            if "region" in raw
            else infer_countries([str(r) for r in raw.get("rules", [])])
        )
    else:
        raise TemplateError(f"{where}：region 写得不对")
    category = str(raw.get("category", ""))
    domains = clean_labels(raw.get("domains", [category]), where, "功能领域", 10)
    template = Template(
        name=name,
        version=version,
        countries=countries,
        domains=domains,
        title=str(raw.get("title") or name),
        description=str(raw.get("description", "")),
        fields=tuple(fields),
        rules=tuple(rules),
        tables=tuple(tables),
        category=domains[0] if domains else category,
        entities=entities,
        references=references,
        workbook=workbook,
        custom_rules=_custom_rules(raw.get("custom_rules"), tables, where),
        titles=_names(raw.get("titles"), where, "模板名"),
    )
    _check_formulas(template, where)
    for i, custom in enumerate(template.custom_rules, start=1):
        try:
            parse_condition(custom.check, template._scope(custom.per_row))
        except FormulaError as err:
            name = custom.title or f"第 {i} 条"
            raise TemplateError(f"{where}：自定义规则「{name}」：{err}") from err
    return template


def _names(value: object, where: str, what: str) -> Names:
    """{语言代码: 名字} → ((代码, 名字), ...)（ADR 0048）。空的名字不要；代码、长度写错说清楚。"""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise TemplateError(f'{where}：{what}的别的语言名字要写成 {{ en = "...", ja = "..." }}')
    found: list[tuple[str, str]] = []
    for lang, text in value.items():
        if not isinstance(lang, str) or not LANGUAGE.match(lang):
            raise TemplateError(f"{where}：{what}的语言代码「{lang}」不对，应该像 en、ja、zh-Hant")
        if not isinstance(text, str):
            raise TemplateError(f"{where}：{what}的 {lang} 名字应该是文字")
        text = text.strip()
        if len(text) > 200:
            raise TemplateError(f"{where}：{what}的 {lang} 名字太长了（最多 200 个字）")
        if text:
            found.append((lang, text))
    return tuple(found)


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
            raise TemplateError(
                f"{where}：字段 {field_name} 的 type 只能是 string、number、date、date_range"
            )
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
        range_export = item.get("range_export", "normalized")
        if range_export not in get_args(RangeExport):
            raise TemplateError(
                f"{where}：字段 {field_name} 的 range_export 只能是 normalized、original、columns"
            )
        separator = item.get("range_separator", " ~ ")
        if not isinstance(separator, str) or not separator.strip() or len(separator) > 10:
            raise TemplateError(f"{where}：字段 {field_name} 的分隔符要写 1～10 个字")
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
                range_export=range_export,
                range_separator=separator,
                names=_names(item.get("names"), where, f"字段 {field_name}"),
            )
        )
    return fields
