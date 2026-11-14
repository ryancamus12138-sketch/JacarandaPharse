"""Templates: which fields to extract from a kind of document and which rules to check.

Three sources, one format:
- .toml files in this folder: built-in templates for evaluation and the CLI (receipt,
  receipt_full), no longer used for web uploads;
- .toml files in starters/: the starter library; "Add to my templates" in the web UI copies one
  into the database;
- My templates in the database: created and edited by users, with a new version on every save;
  the definition is JSON with the same structure as .toml.

Why files rather than code: changing fields or descriptions, or adding a new kind of document,
needs no Python changes; templates are versioned and every document records which version of
which template it was extracted with (every extraction records its provenance).

TOML: Python ships tomllib, so no new dependency is needed; easier to write than JSON and
supports comments."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, replace
from functools import cache
from importlib.resources import files
from typing import Any, Literal, get_args

from jacara.core.daterange import ORDER_PROBLEM, Parsed, day_first_for, parse_date_range
from jacara.core.i18n import join_items, nested, stored
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
"""Field name: starts with a lowercase letter; only lowercase letters, digits and underscores (used
in the database, exports and the API)."""

COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")
"""Country or region code: ISO 3166-1 two uppercase letters (CN, AU, US, …)."""

_OLD_REGIONS = {"": (), "cn": ("CN",), "au": ("AU",)}
"""Legacy notation (region = "cn"), for reading old data."""


def infer_countries(rule_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Templates without a country (saved before countries existed, or imported from Excel or older
    template files): derived from the selected rules. The countries of the selected country rules
    are the template's countries; none selected means generic (empty)."""
    return tuple(sorted({REGIONS[r] for r in rule_ids if r in REGIONS}))


def clean_labels(values: object, where: str, what: str, limit: int = 20) -> tuple[str, ...]:
    """A tag list: strings with surrounding whitespace stripped, empty and duplicate entries
    removed, at most 20 characters each."""
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)) or not all(isinstance(v, str) for v in values):
        raise TemplateError(stored("{where}：{what}应为文字列表", where=where, what=what))
    cleaned = tuple(dict.fromkeys(v.strip()[:20] for v in values if v.strip()))
    if len(cleaned) > limit:
        raise TemplateError(
            stored("{where}：{what}最多 {limit} 个", where=where, what=what, limit=limit)
        )
    return cleaned


def clean_countries(values: object, where: str) -> tuple[str, ...]:
    """Country or region code: uppercase, format checked. Empty means generic."""
    codes = tuple(c.upper() for c in clean_labels(values, where, stored("国家和地区"), 300))
    wrong = [c for c in codes if not COUNTRY_CODE.match(c)]
    if wrong:
        raise TemplateError(
            stored(
                ("{where}：国家和地区需使用两位字母代码（如 CN、AU），无法识别：{codes}"),
                where=where,
                codes=join_items(wrong),
            )
        )
    return codes


DEFAULT_TEMPLATE = "receipt"
"""Default template for uploaded files."""


class TemplateError(ValueError):
    """Template not found, or the template file is invalid. The message is shown to the user as
    is."""


RangeExport = Literal["normalized", "original", "columns"]
"""Date range export: uniform format, source text, or two columns."""

LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3}|-[A-Z]{2})?$")
"""Language code (short BCP 47): zh, en, ja, ko, de, zh-Hant, …"""

DEFAULT_LANGUAGE = "zh"
"""Primary language of templates that do not set one: starter templates are written in Chinese."""

Names = tuple[tuple[str, str], ...]
"""Texts in other languages: ((language code, text), ...). label, title and description themselves
are in the template's primary language."""

DOMAIN_NAMES: dict[str, dict[str, str]] = {
    "发票": {"en": "Invoice"},
    "收据": {"en": "Receipt"},
    "购物": {"en": "Shopping"},
    "餐饮": {"en": "Dining"},
    "住宿": {"en": "Lodging"},
    "交通": {"en": "Transport"},
    "物流": {"en": "Logistics"},
    "外贸单证": {"en": "Trade documents"},
    "采购": {"en": "Procurement"},
    "医疗": {"en": "Medical"},
    "检验报告": {"en": "Lab report"},
    "质检": {"en": "Quality inspection"},
}
"""Domains used by the starter library (Chinese) → names in other languages. Domains of my templates
are free text and only translated when they are one of these."""


PROMPT_LANGUAGES: dict[str, str] = {
    "CN": "zh", "TW": "zh-Hant", "HK": "zh-Hant", "JP": "ja", "KR": "ko", "MY": "ms",
    "VN": "vi", "IN": "hi", "TH": "th", "ID": "id", "DE": "de", "AT": "de", "FR": "fr",
    "IT": "it", "ES": "es", "NL": "nl", "PL": "pl", "CA": "fr", "AE": "ar", "SA": "ar",
}  # fmt: skip
"""Country → local languages common on its documents. Model prompts include only English names and
names in these languages:"""


def prompt_languages(countries: tuple[str, ...]) -> frozenset[str]:
    """Generic templates give English names only; country templates add local languages. Switching
    the UI language is unaffected."""
    return frozenset({"en", *(PROMPT_LANGUAGES[c] for c in countries if c in PROMPT_LANGUAGES)})


def name_in(label: str, names: Names, language: str | None) -> str:
    """Name in this language; label when no language is given or the language is not defined."""
    if not language:
        return label
    return dict(names).get(language, label)


@dataclass(frozen=True)
class TemplateField:
    name: str
    """Field ID: never changes (the display name may). The database, rules and export alignment
    depend on it."""
    label: str
    """Human-readable name, for example "Total". Also the column header in Excel exports."""
    type: FieldType
    required: bool
    description: str
    """Description for the LLM."""
    aliases: tuple[str, ...] = ()
    """Other notations on documents (Check-in, Arrival, 入住日期), passed to the model as well."""
    export: bool = True
    """Whether this column is included in Excel exports."""
    sensitive: bool = False
    """Sensitive information (identity document numbers and the like): not exported by default."""
    options: tuple[str, ...] = ()
    """Allowed values (for example from Excel drop-downs): warn when the extracted value is not
    among them. Empty means unrestricted."""
    extract: bool = True
    """Whether the model extracts this field from the document. False = manual entry (claimant,
    department, …): not sent to the model; entered during review."""
    unique: bool = False
    """Must be unique (invoice numbers, bill numbers): warn "possible duplicate claim" when another
    document has the same value."""
    compute: str = ""
    """Formula of a computed field, for example check_out - check_in. Fields with a formula are not
    sent to the model but computed from other fields."""
    range_export: RangeExport = "normalized"
    """Date range export format: normalized (2014-07-01 ~ 2014-07-31), original (source text),
    columns (separate start and end columns). Not used by other types."""
    range_separator: str = " ~ "
    """Separator between start and end in the normalized format."""
    names: Names = ()
    """Names in other languages (English, local languages): switchable on the review page and in
    exports; also tells the model how the document may label it."""
    descriptions: Names = ()
    """Description in other languages (shown in previews; the primary language one is given to the
    model)."""

    def label_in(self, language: str | None) -> str:
        return name_in(self.label, self.names, language)

    @property
    def asks_model(self) -> bool:
        """Extracted by the model from the document: neither manual entry nor computed."""
        return self.extract and not self.compute

    @property
    def spec(self) -> FieldSpec:
        return self.spec_in(None)

    def spec_in(self, languages: frozenset[str] | None) -> FieldSpec:
        """Field format understood by the extractor. Aliases and allowed values go into the
        description so the model knows the other notations and the permitted values. languages:
        only names in these languages are included (None = all)."""
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
    """A table (for example line items): every row has the same columns; the number of rows depends
    on the document."""

    name: str
    label: str
    description: str
    columns: tuple[TemplateField, ...]
    required: bool = False
    """At least one row is required."""
    export: bool = True
    names: Names = ()
    """Table name in other languages."""
    descriptions: Names = ()
    """Table description in other languages."""

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
    """Custom rule: a condition such as sum(items.amount) == total.

        When per_row names a table, each row is checked separately and the condition uses that
    table's column names (quantity * unit_price == amount)."""

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
    """Computed fields of a template, ordered by dependency (inputs first)."""

    fields: dict[str, Formula]
    """Formulas of regular fields."""
    columns: dict[str, dict[str, Formula]]
    """Table name → {column name → per-row formula}."""

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
    """Type label: invoice, transport, receipt, lodging, … Used to group templates at upload."""
    enforce_required: bool = False
    """Whether a missing required field counts as an error. Enabled for starter templates and my
    templates; disabled for built-in templates (evaluation and CLI) to keep their results
    unchanged."""
    entities: tuple[TemplateEntity, ...] = ()
    """Subjects: several documents referring to the same person or company are merged into one row
    by identifier on export."""
    references: tuple[TemplateReference, ...] = ()
    """Reference tables: fixed lists used for matching and filling identifiers."""
    workbook: Workbook | None = None
    """Layout for export to the original workbook. The standard export format is used without it."""
    custom_rules: tuple[CustomRule, ...] = ()
    """Custom rules, checked together with the selected built-in rules."""
    countries: tuple[str, ...] = ()
    """Countries and regions: empty means generic. For my templates, the templates table in the
    database is authoritative."""
    domains: tuple[str, ...] = ()
    """Domains (invoice, transport, receipt, lodging, …); several allowed."""
    titles: Names = ()
    """Template name in other languages."""
    language: str = DEFAULT_LANGUAGE
    """Primary language: the language of title, description and every label and description.
    Chosen per template; starter templates are written in Chinese and switched with
    with_language()."""
    descriptions: Names = ()
    """Template description in other languages."""
    basis: tuple[str, ...] = ()
    """Basis of a starter template: regulations, use cases and sources (from the comment at the top
    of the template file), shown in the preview."""

    @property
    def languages(self) -> tuple[str, ...]:
        """Switchable languages: the primary language first, then in the order given in the
        template."""
        groups: list[Names] = [self.titles, *(f.names for f in self.fields)]
        for t in self.tables:
            groups += [t.names, *(c.names for c in t.columns)]
        found = dict.fromkeys([self.language])
        for names in groups:
            found.update(dict.fromkeys(lang for lang, _ in names))
        return tuple(found)

    def title_in(self, language: str | None) -> str:
        return name_in(self.title, self.titles, language)

    @property
    def day_first(self) -> bool | None:
        """Whether numeric dates are day/month/year (by country), used when splitting date ranges.
        None if unknown."""
        return day_first_for(self.countries)

    def ranges(self) -> dict[str, TemplateField]:
        """Date range fields: field ID → field; table columns as items.period."""
        found = {f.name: f for f in self.fields if f.type == "date_range"}
        for t in self.tables:
            found |= {f"{t.name}.{c.name}": c for c in t.columns if c.type == "date_range"}
        return found

    def foreign_rules(self) -> list[str]:
        """Selected rules not belonging to this template's countries (starter library requirement:
        country templates use only generic rules and their own country's rules; generic templates
        only generic rules). My templates are unrestricted; this check is only for starter
        templates."""
        return [
            r.title for r in self.rules if r.id in REGIONS and REGIONS[r.id] not in self.countries
        ]

    def all_rules(self) -> tuple[Rule, ...]:
        """Selected built-in rules + custom rules (ids custom_1, custom_2, …) + date range
        checks."""
        return (
            self.rules
            + tuple(self._custom(i, c) for i, c in enumerate(self.custom_rules, start=1))
            + self._range_rules()
        )

    def _range_rules(self) -> tuple[Rule, ...]:
        """Two checks per date range field: not splittable or day/month ambiguous (warning), end
        before start (error)."""
        day_first = self.day_first
        rules: list[Rule] = []
        places = [(f, f.name, None, f.label) for f in self.fields]
        places += [
            (c, c.name, t.name, stored("{table}的{column}", table=t.label, column=c.label))
            for t in self.tables
            for c in t.columns
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
                return stored(
                    "{label}：{problem}，请核对开始和结束日期",
                    label=label,
                    problem=nested(found.problem),
                )

            def ordered(values: dict[str, Any], label: str = label, parsed=parsed) -> str | None:
                found = parsed(values)
                if found is None or found.problem != ORDER_PROBLEM or found.range is None:
                    return None
                return stored(
                    "{label}：结束 {end} 早于开始 {start}",
                    label=label,
                    end=found.range.end,
                    start=found.range.start,
                )

            rules.append(
                Rule(
                    f"range_{name}",
                    stored("{label}可拆分", label=label),
                    (name,),
                    readable,
                    "warning",
                    per_row,
                )
            )
            rules.append(
                Rule(
                    f"range_order_{name}",
                    stored("{label}结束不早于开始", label=label),
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
            return stored(
                ("{left} 为 {left_value}，{right} 为 {right_value}，不满足 {condition}"),
                left=left_text,
                left_value=_show(verdict.left),
                right=right_text,
                right_value=_show(verdict.right),
                condition=readable,
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
        """Fields sent to the model for extraction (manual-entry fields excluded)."""
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
        """Manual-entry regular fields: an empty value is stored during processing and filled during
        review."""
        return tuple(f for f in self.fields if not f.extract and not f.compute)

    def formulas(self) -> Formulas:
        """Validated formulas. Already validated when the template was loaded, so this cannot
        fail."""
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
        """Validator of this template: selected rules + required + allowed values + reference table
        matching."""
        options = [(f.name, f.label, f.options) for f in self.fields if f.options]
        for t in self.tables:
            options += [
                (f"{t.name}.*.{c.name}", c.label, c.options) for c in t.columns if c.options
            ]
        # Table columns (items.lab_name) are matched per row: items.*.lab_name
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
        """Field ID → display name (default language; with language set, names in that language,
        falling back to the default). Tables appear as items (table name) and items.amount
        (column)."""
        labels = {f.name: f.label_in(language) for f in self.fields}
        for t in self.tables:
            labels[t.name] = t.label_in(language)
            labels.update({f"{t.name}.{c.name}": c.label_in(language) for c in t.columns})
        return labels

    def definition(self) -> dict[str, Any]:
        """Dictionary with the same structure as .toml: this is what is stored in the database as a
        version of my template."""
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
                    **({"descriptions": dict(t.descriptions)} if t.descriptions else {}),
                }
                for t in self.tables
            ],
            "rules": [r.id for r in self.rules],
            "entities": [entity_dict(e) for e in self.entities],
            "references": [reference_dict(r) for r in self.references],
            "workbook": workbook_dict(self.workbook) if self.workbook else None,
            "custom_rules": [c.as_dict() for c in self.custom_rules],
            **({"titles": dict(self.titles)} if self.titles else {}),
            **({"descriptions": dict(self.descriptions)} if self.descriptions else {}),
            **({"language": self.language} if self.language != DEFAULT_LANGUAGE else {}),
        }


def _tables_of(values: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Cells such as items.2.amount → table name → rows of {column name → value}."""
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
        **({"descriptions": dict(f.descriptions)} if f.descriptions else {}),
        **(
            {"range_export": f.range_export, "range_separator": f.range_separator}
            if f.type == "date_range"
            else {}
        ),
    }


def template_names() -> list[str]:
    """Names of built-in templates (evaluation and CLI), sorted alphabetically."""
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
    # Statutory documents of individual countries and regions
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
"""Starter library, in display order."""


def load_starter(name: str) -> Template:
    """Load a starter template (a .toml file in starters/) as written: in Chinese."""
    return load_starter_in(name, DEFAULT_LANGUAGE)


@cache
def load_starter_in(name: str, language: str) -> Template:
    """Load a starter template with language as its primary language (see with_language). The
    notes at the top of the file come from its [basis] table for languages other than Chinese."""
    if name not in STARTERS:
        raise TemplateError(
            stored(
                "不存在名为 {name} 的起步模板，现有：{known}", name=name, known=join_items(STARTERS)
            )
        )
    path = files(__package__).joinpath("starters", f"{name}.toml")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise TemplateError(
            stored("起步模板 {name}.toml 格式错误：{err}", name=name, err=err)
        ) from err
    text = path.read_text(encoding="utf-8")
    translated_basis = raw.pop("basis", {})
    basis = (
        header_notes(text)
        if language == DEFAULT_LANGUAGE
        else tuple(translated_basis.get(language) or header_notes(text))
    )
    return replace(
        parse_template(with_language(raw, language), name), enforce_required=True, basis=basis
    )


def with_language(raw: dict[str, Any], language: str) -> dict[str, Any]:
    """A template definition (as read from a file or stored) with language as its primary
    language. Every text that has a version in that language moves into label, title or
    description, and the previous primary text moves into names, titles or descriptions under the
    previous primary language, so nothing is lost and switching back restores the original. Texts
    without a version in that language stay as they are. Domains in DOMAIN_NAMES are translated."""
    previous = raw.get("language") or DEFAULT_LANGUAGE
    if language == previous:
        return raw
    out = dict(raw)
    _swap(out, "title", "titles", previous, language)
    _swap(out, "description", "descriptions", previous, language)
    out["fields"] = [_swap_field(f, previous, language) for f in raw.get("fields", [])]
    out["tables"] = [
        {
            **_swap_field(t, previous, language),
            "columns": [_swap_field(c, previous, language) for c in t.get("columns", [])],
        }
        for t in raw.get("tables", [])
    ]
    if "domains" in raw:
        out["domains"] = [domain_in(d, previous, language) for d in raw["domains"]]
    if "category" in raw:
        out["category"] = domain_in(raw["category"], previous, language)
    out["language"] = language
    return out


def domain_in(domain: str, previous: str, language: str) -> str:
    """A domain written in the previous language, in language; unchanged when DOMAIN_NAMES does
    not know it."""
    for chinese, names in DOMAIN_NAMES.items():
        written = chinese if previous == DEFAULT_LANGUAGE else names.get(previous)
        if written == domain:
            return chinese if language == DEFAULT_LANGUAGE else names.get(language, domain)
    return domain


def _swap_field(item: dict[str, Any], previous: str, language: str) -> dict[str, Any]:
    out = dict(item)
    _swap(out, "label", "names", previous, language)
    _swap(out, "description", "descriptions", previous, language)
    return out


def _swap(item: dict[str, Any], key: str, others_key: str, previous: str, language: str) -> None:
    """Move others_key[language] into key and key into others_key[previous] (in place)."""
    others = dict(item.get(others_key) or {})
    text = others.pop(language, None)
    if not text:
        return
    current = item.get(key)
    if current:
        others[previous] = current
    item[key] = text
    if others:
        item[others_key] = others
    else:
        item.pop(others_key, None)


def header_notes(text: str) -> tuple[str, ...]:
    """Comment at the top of a template file → list of notes. Indented lines, and lines following an
    unfinished line (not ending with a period), are joined to the previous note; the "fields only
    …" sentence is dropped."""
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
    """A version of my template from the database → Template. The definition has the same format and
    validation as .toml."""
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
    """Load a template. Cached after the first load; changes to the file take effect after a
    restart."""
    path = files(__package__).joinpath(f"{name}.toml")
    if not path.is_file():
        raise TemplateError(
            stored(
                "不存在名为 {name} 的模板，现有：{known}",
                name=name,
                known=join_items(template_names()),
            )
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise TemplateError(stored("模板 {name}.toml 格式错误：{err}", name=name, err=err)) from err
    return parse_template(raw, name)


def parse_template(raw: dict[str, Any], name: str, *, check_name: bool = True) -> Template:
    """Validate a dictionary read from TOML and turn it into a Template. Errors name the offending
    entry."""
    where = (
        stored("模板 {name}", name=name)
        if check_name
        else stored("模板「{title}」", title=raw.get("title") or name)
    )
    if check_name and raw.get("name") != name:
        raise TemplateError(stored("{where}：文件中的 name 应为 {name}", where=where, name=name))
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise TemplateError(stored("{where}：version 应为大于 0 的整数", where=where))

    fields = _fields(raw.get("fields", []), where)
    if not fields:
        raise TemplateError(stored("{where}：至少需要一个字段", where=where))

    tables: list[TemplateTable] = []
    for i, item in enumerate(raw.get("tables", []), start=1):
        table_name = item.get("name")
        if not isinstance(table_name, str) or not _FIELD_NAME.match(table_name):
            raise TemplateError(
                stored(
                    "{where}：第 {i} 张表的 name 只能包含小写英文字母、数字和下划线",
                    where=where,
                    i=i,
                )
            )
        if any(f.name == table_name for f in fields) or any(t.name == table_name for t in tables):
            raise TemplateError(
                stored(
                    "{where}：表 {table_name} 与其他字段或表重名",
                    where=where,
                    table_name=table_name,
                )
            )
        columns = _fields(
            item.get("columns", []),
            stored("{where} 的表 {table_name}", where=where, table_name=table_name),
        )
        if not columns:
            raise TemplateError(
                stored("{where}：表 {table_name} 至少需要一列", where=where, table_name=table_name)
            )
        if any(c.name == "row" for c in columns):
            raise TemplateError(
                stored(
                    "{where}：表 {table_name} 的列不能命名为 row（保留用于行号）",
                    where=where,
                    table_name=table_name,
                )
            )
        tables.append(
            TemplateTable(
                name=table_name,
                label=str(item.get("label") or table_name),
                description=str(item.get("description", "")),
                columns=tuple(columns),
                required=bool(item.get("required", False)),
                export=bool(item.get("export", True)),
                names=_names(
                    item.get("names"), where, stored("表 {table_name}", table_name=table_name)
                ),
                descriptions=_names(
                    item.get("descriptions"),
                    where,
                    stored("表 {table_name}", table_name=table_name),
                    description=True,
                ),
            )
        )

    rules: list[Rule] = []
    for rule_id in raw.get("rules", []):
        if rule_id not in RULES:
            raise TemplateError(
                stored(
                    "{where}：不存在编号为 {rule_id} 的规则，现有：{known}",
                    where=where,
                    rule_id=rule_id,
                    known=join_items(RULES),
                )
            )
        rules.append(RULES[rule_id])

    field_ids = {f.name for f in fields}
    refs = field_ids | {f"{t.name}.{c.name}" for t in tables for c in t.columns}
    try:
        entities = parse_entities(raw.get("entities"), field_ids, where)
        references = parse_references(raw.get("references"), refs, where)
        # A column of the original workbook may hold the start, end or number of days of a date
        # range.
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
        raise TemplateError(stored("{where}：region 格式错误", where=where))
    category = str(raw.get("category", ""))
    domains = clean_labels(raw.get("domains", [category]), where, stored("功能领域"), 10)
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
        titles=_names(raw.get("titles"), where, stored("模板名称")),
        language=_language(raw.get("language"), where),
        descriptions=_names(raw.get("descriptions"), where, stored("模板"), description=True),
    )
    _check_formulas(template, where)
    for i, custom in enumerate(template.custom_rules, start=1):
        try:
            parse_condition(custom.check, template._scope(custom.per_row))
        except FormulaError as err:
            name = custom.title or stored("第 {i} 条", i=i)
            raise TemplateError(
                stored("{where}：自定义规则「{name}」：{err}", where=where, name=name, err=err)
            ) from err
    return template


def _language(value: object, where: str) -> str:
    """Primary language of a template; absent means the default (Chinese)."""
    if value is None or value == "":
        return DEFAULT_LANGUAGE
    if not isinstance(value, str) or not LANGUAGE.match(value):
        raise TemplateError(
            stored(
                ("{where}：主语言「{language}」无效，格式应如 zh、en、ja、zh-Hant"),
                where=where,
                language=value,
            )
        )
    return value


def _names(value: object, where: str, what: str, *, description: bool = False) -> Names:
    """{language code: text} → ((code, text), ...). Empty texts are dropped; invalid codes and
    lengths are reported. description: the texts are descriptions (up to 2000 characters) rather
    than names (up to 200)."""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise TemplateError(
            stored(
                ('{where}：{what}的其他语言名称需写作 {{ en = "...", ja = "..." }}'),
                where=where,
                what=what,
            )
        )
    found: list[tuple[str, str]] = []
    for lang, text in value.items():
        if not isinstance(lang, str) or not LANGUAGE.match(lang):
            raise TemplateError(
                stored(
                    ("{where}：{what}的语言代码「{lang}」无效，格式应如 en、ja、zh-Hant"),
                    where=where,
                    what=what,
                    lang=lang,
                )
            )
        if not isinstance(text, str):
            raise TemplateError(
                stored("{where}：{what}的 {lang} 名称应为文字", where=where, what=what, lang=lang)
            )
        text = text.strip()
        if description and len(text) > 2000:
            raise TemplateError(
                stored(
                    "{where}：{what}的 {lang} 说明过长（最多 2000 个字符）",
                    where=where,
                    what=what,
                    lang=lang,
                )
            )
        if not description and len(text) > 200:
            raise TemplateError(
                stored(
                    "{where}：{what}的 {lang} 名称过长（最多 200 个字符）",
                    where=where,
                    what=what,
                    lang=lang,
                )
            )
        if text:
            found.append((lang, text))
    return tuple(found)


def _custom_rules(items: Any, tables: list[TemplateTable], where: str) -> tuple[CustomRule, ...]:
    if items is None:
        return ()
    if not isinstance(items, list):
        raise TemplateError(stored("{where}：custom_rules 应为列表", where=where))
    rules: list[CustomRule] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise TemplateError(stored("{where}：第 {i} 条自定义规则格式错误", where=where, i=i))
        check = item.get("check")
        if not isinstance(check, str) or not check.strip():
            raise TemplateError(stored("{where}：第 {i} 条自定义规则缺少条件", where=where, i=i))
        severity = item.get("severity", "error")
        if severity not in ("error", "warning"):
            raise TemplateError(
                stored(
                    ("{where}：第 {i} 条自定义规则的 severity 只能为 error 或 warning"),
                    where=where,
                    i=i,
                )
            )
        per_row = item.get("per_row") or None
        if per_row is not None and per_row not in {t.name for t in tables}:
            raise TemplateError(
                stored("{where}：第 {i} 条自定义规则的 per_row 不是此模板中的表", where=where, i=i)
            )
        rules.append(
            CustomRule(str(item.get("title") or "").strip(), check.strip(), severity, per_row)
        )
    return tuple(rules)


def _check_formulas(template: Template, where: str) -> None:
    """Validate each formula; errors name the field."""
    fields = {f.name: f.type for f in template.fields}
    tables = {t.name: {c.name: c.type for c in t.columns} for t in template.tables}
    places = [(f, Scope(fields, tables), f.label) for f in template.fields]
    places += [
        (
            c,
            Scope(fields, tables, tables[t.name]),
            stored("{table}的{column}", table=t.label, column=c.label),
        )
        for t in template.tables
        for c in t.columns
    ]
    for field, scope, label in places:
        if not field.compute:
            continue
        try:
            formula = parse_formula(field.compute, scope)
        except FormulaError as err:
            raise TemplateError(
                stored("{where}：「{label}」的公式：{err}", where=where, label=label, err=err)
            ) from err
        if scope.row is not None and formula.fields & {
            f.name for f in template.fields if f.compute
        }:
            raise TemplateError(
                stored(
                    ("{where}：「{label}」的公式不能引用其他计算字段（仅可引用从单据抽取的字段）"),
                    where=where,
                    label=label,
                )
            )
    try:
        template.formulas()
    except FormulaError as err:
        raise TemplateError(stored("{where}：{err}", where=where, err=err)) from err


def _fields(items: list[dict[str, Any]], where: str) -> list[TemplateField]:
    """Validate a group of fields (template fields, or the columns of a table)."""
    fields: list[TemplateField] = []
    for i, item in enumerate(items, start=1):
        field_name = item.get("name")
        if not isinstance(field_name, str) or not _FIELD_NAME.match(field_name):
            raise TemplateError(
                stored(
                    ("{where}：第 {i} 个字段的 name 只能包含小写英文字母、数字和下划线"),
                    where=where,
                    i=i,
                )
            )
        if item.get("type") not in get_args(FieldType):
            raise TemplateError(
                stored(
                    ("{where}：字段 {field_name} 的 type 只能为 string、number、date、date_range"),
                    where=where,
                    field_name=field_name,
                )
            )
        if any(f.name == field_name for f in fields):
            raise TemplateError(
                stored("{where}：字段 {field_name} 重复", where=where, field_name=field_name)
            )
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise TemplateError(
                stored(
                    "{where}：字段 {field_name} 的 aliases 应为文字列表",
                    where=where,
                    field_name=field_name,
                )
            )
        sensitive = bool(item.get("sensitive", False))
        options = item.get("options", [])
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            raise TemplateError(
                stored(
                    "{where}：字段 {field_name} 的 options 应为文字列表",
                    where=where,
                    field_name=field_name,
                )
            )
        compute = item.get("compute", "")
        if not isinstance(compute, str):
            raise TemplateError(
                stored(
                    "{where}：字段 {field_name} 的 compute 应为一条公式",
                    where=where,
                    field_name=field_name,
                )
            )
        if compute.strip() and item["type"] != "number":
            raise TemplateError(
                stored(
                    "{where}：字段 {field_name} 含公式，type 需为 number",
                    where=where,
                    field_name=field_name,
                )
            )
        range_export = item.get("range_export", "normalized")
        if range_export not in get_args(RangeExport):
            raise TemplateError(
                stored(
                    (
                        "{where}：字段 {field_name} 的 range_export 只能为 normalized"
                        "、original、columns"
                    ),
                    where=where,
                    field_name=field_name,
                )
            )
        separator = item.get("range_separator", " ~ ")
        if not isinstance(separator, str) or not separator.strip() or len(separator) > 10:
            raise TemplateError(
                stored(
                    "{where}：字段 {field_name} 的分隔符需为 1～10 个字符",
                    where=where,
                    field_name=field_name,
                )
            )
        fields.append(
            TemplateField(
                name=field_name,
                label=str(item.get("label") or field_name),
                type=item["type"],
                required=bool(item.get("required", False)),
                description=str(item.get("description", "")),
                aliases=tuple(a.strip() for a in aliases if a.strip()),
                # Sensitive fields are not exported by default; an explicit export setting takes
                # precedence.
                export=bool(item.get("export", not sensitive)),
                sensitive=sensitive,
                options=tuple(dict.fromkeys(o.strip() for o in options if o.strip())),
                extract=bool(item.get("extract", True)),
                compute=compute.strip(),
                unique=bool(item.get("unique", False)),
                range_export=range_export,
                range_separator=separator,
                names=_names(
                    item.get("names"), where, stored("字段 {field_name}", field_name=field_name)
                ),
                descriptions=_names(
                    item.get("descriptions"),
                    where,
                    stored("字段 {field_name}", field_name=field_name),
                    description=True,
                ),
            )
        )
    return fields
