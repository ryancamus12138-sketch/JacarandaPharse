"""Request and response schemas of the templates API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from jacara.core.i18n import Rendered

TemplateKind = Literal["mine", "starter", "builtin"]
"""mine: my templates (in the database, editable); starter: starter template library; builtin:
built-in (used by evaluation and the CLI)."""


class TemplateFieldOut(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    description: str
    aliases: list[str] = []
    export: bool = True
    sensitive: bool = False
    options: list[str] = []
    extract: bool = True
    compute: str = ""
    """Formula of a computed field; empty if not computed."""
    unique: bool = False
    """Must be unique: warn when another document has the same value (possible duplicate claim)."""
    range_export: str = "normalized"
    """Date range export: normalized (uniform format), original (source text), columns (two
    columns)."""
    range_separator: str = " ~ "
    names: dict[str, str] = {}
    """Names in other languages: language code → name. label is in the template's primary
    language."""
    descriptions: dict[str, str] = {}
    """Description in other languages: language code → description."""


class TemplateRuleOut(BaseModel):
    id: str
    title: str
    needs: list[str]
    severity: str


class TemplateTableOut(BaseModel):
    name: str
    label: str
    description: str
    columns: list[TemplateFieldOut]
    required: bool = False
    export: bool = True
    names: dict[str, str] = {}
    descriptions: dict[str, str] = {}


class EntityOut(BaseModel):
    """Subjects: several documents referring to the same person or company are merged into one row
    by identifier on export."""

    name: str
    label: str
    key: str
    fields: list[str]


class ReferenceOut(BaseModel):
    """Reference table (summary only; the data may have thousands of rows)."""

    name: str
    label: str
    columns: list[str]
    row_count: int
    match_field: str
    match_column: str


class WorkbookColumnOut(BaseModel):
    index: int
    header: str
    ref: str


class WorkbookSheetOut(BaseModel):
    name: str
    role: str
    header_row: int
    same_as: str = ""
    table: str = ""
    entity: str = ""
    reference: str = ""
    columns: list[WorkbookColumnOut] = []


class WorkbookOut(BaseModel):
    """Layout for export to the original workbook: original file name, role of each sheet and
    content of each column."""

    filename: str
    sheets: list[WorkbookSheetOut]


class TemplateVersionOut(BaseModel):
    version: int
    note: Rendered
    created_at: datetime


class CustomRuleIO(BaseModel):
    """Custom rule: a condition such as sum(items.amount) == total."""

    title: str = Field(default="", max_length=100)
    check: str = Field(min_length=1, max_length=500)
    severity: Literal["error", "warning"] = "error"
    per_row: str | None = None
    """Table name: each row is checked separately, and the condition uses this table's column
    names."""


class FormulaCheckIn(BaseModel):
    """Live validation of a formula or rule condition in the editor, using the fields currently in
    the editor (unsaved changes included)."""

    text: str = Field(max_length=500)
    kind: Literal["formula", "condition"] = "formula"
    fields: list[TemplateFieldIn] = Field(default=[], max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    row: str | None = None
    """For table column formulas and per-row rules: the table name; names resolve to this table's
    columns."""


class FormulaCheckOut(BaseModel):
    ok: bool
    readable: str = ""
    """Form with display names: check_out - check_in → Check-out date − Check-in date."""
    message: str = ""
    """Description of the error."""


class TemplateOut(BaseModel):
    name: str
    """File name for built-in and starter templates (receipt, cn_einvoice); id for my templates."""
    version: int
    title: str
    titles: dict[str, str] = {}
    """Template name in other languages."""
    language: str = "zh"
    """Primary language: the language of title, description and every label and description."""
    descriptions: dict[str, str] = {}
    """Template description in other languages."""
    languages: list[str] = ["zh"]
    """Available languages; the first is the primary language."""
    basis: list[str] = []
    """Basis of a starter template: regulations, use cases and sources. Empty for my templates."""
    description: str
    fields: list[TemplateFieldOut]
    rules: list[TemplateRuleOut]
    custom_rules: list[CustomRuleIO] = []
    domains: list[str] = []
    """Domains: invoice, transport, receipt, … (several allowed)."""
    countries: list[str] = []
    """Country or region code (CN, AU, …); empty means generic."""
    tags: list[str] = []
    """Custom tags. Starter templates have none."""
    tables: list[TemplateTableOut] = []
    """Tables (for example line items). Each cell is stored as a field named like items.2.amount."""
    kind: TemplateKind = "builtin"
    category: str = ""
    id: uuid.UUID | None = None
    """My templates only."""
    archived: bool = False
    source: str = ""
    updated_at: datetime | None = None
    versions: list[TemplateVersionOut] = []
    """Version history of my template (only in the single-template response)."""
    starter_formulas: dict[str, str] = {}
    """For copies of a starter template: formulas the starter template has since added (field ID →
    formula) that this copy lacks. The editor offers to apply them. Table columns are written as
    items.amount."""
    used: int = 0
    """Number of documents that have used this template (processed or queued). My templates only."""
    last_used_at: datetime | None = None
    """Last time a document used it (for "recently used" sorting on the templates page)."""
    entities: list[EntityOut] = []
    references: list[ReferenceOut] = []
    workbook: WorkbookOut | None = None


class RuleInfo(BaseModel):
    """A rule selectable in the template editor. needs lists the field IDs it uses."""

    id: str
    title: str
    needs: list[str]
    severity: str
    per_row: str | None = None
    requires: list[str] = []
    """The rule applies only when the template contains these fields (values may be empty)."""
    country: str = ""
    """Country-specific rule: country code (CN, AU); empty for generic rules."""


class TemplateFieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    """Field ID: starts with a lowercase letter; only lowercase letters, digits and underscores. Do
    not change after saving."""
    label: str = Field(min_length=1, max_length=100)
    type: Literal["string", "number", "date", "date_range"] = "string"
    required: bool = False
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default=[], max_length=20)
    export: bool = True
    sensitive: bool = False
    options: list[str] = Field(default=[], max_length=500)
    """Allowed values: warn when the extracted value is not among them. Empty means unrestricted."""
    extract: bool = True
    """False = manual entry: not extracted by the model; entered during review."""
    compute: str = Field(default="", max_length=500)
    """Formula of a computed field, for example check_out - check_in. Fields with a formula are not
    extracted by the model but computed from other fields."""
    unique: bool = False
    """Must be unique (for example invoice numbers): warn when another document has the same
    value."""
    range_export: Literal["normalized", "original", "columns"] = "normalized"
    """Date range export: uniform format, source text, or separate start and end columns. Not used
    by other types."""
    range_separator: str = Field(default=" ~ ", min_length=1, max_length=10)
    names: dict[str, str] = Field(default={}, max_length=30)
    """Names in other languages: {"en": "Invoice number", "ja": "請求書番号"}."""
    descriptions: dict[str, str] = Field(default={}, max_length=30)
    """Description in other languages."""


class TemplateTableIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    required: bool = False
    export: bool = True
    names: dict[str, str] = Field(default={}, max_length=30)
    descriptions: dict[str, str] = Field(default={}, max_length=30)
    columns: list[TemplateFieldIn] = Field(min_length=1, max_length=30)


class TemplateIn(BaseModel):
    """Save a template (every save creates a new version)."""

    title: str = Field(min_length=1, max_length=100)
    titles: dict[str, str] = Field(default={}, max_length=30)
    """Template name in other languages."""
    language: str = Field(default="zh", max_length=16)
    """Primary language of title, description and every label and description."""
    descriptions: dict[str, str] = Field(default={}, max_length=30)
    """Template description in other languages."""
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    rules: list[str] = []
    custom_rules: list[CustomRuleIO] = Field(default=[], max_length=30)
    domains: list[str] = Field(default=[], max_length=10)
    """Domains (several allowed)."""
    countries: list[str] = Field(default=[], max_length=300)
    """Country or region code; empty means generic."""
    tags: list[str] = Field(default=[], max_length=20)
    """Custom tags."""
    note: str = Field(default="", max_length=500)
    """What changed in this version."""
    column_refs: dict[str, str] = Field(default={}, max_length=500)
    """Templates created from Excel: change what a column of the original workbook is filled with.
    Keys are "sheet name::header"; values are a field ID, period.start / period.end / period.days
    (start, end and number of days of a date range), or empty (leave blank). Omitted columns are
    unchanged."""


class LabelsIn(BaseModel):
    """Change tags only (no new version). Omitted groups are unchanged."""

    domains: list[str] | None = Field(default=None, max_length=10)
    countries: list[str] | None = Field(default=None, max_length=300)
    tags: list[str] | None = Field(default=None, max_length=20)


class TemplateCreate(BaseModel):
    """Create a template: blank, a copy of a starter template, or a copy of another of my
    templates."""

    source: Literal["blank", "starter", "copy"]
    starter: str | None = None
    copy_of: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=100)
    content: TemplateIn | None = None
    """Blank template: created only when saved from the editor; the content (version 1) is given
    here."""


class ArchiveIn(BaseModel):
    archived: bool


class SampleFieldIn(TemplateFieldIn):
    """A field selected from a sample. source is the key-value field name in the sample (kv.3);
    empty for added fields."""

    source: str | None = None


class SampleIn(BaseModel):
    """Create a template from a sample: the sample document (with extracted key-value pairs) plus
    the selection and names."""

    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[SampleFieldIn] = Field(min_length=1, max_length=80)
    rules: list[str] = []


class BoxFieldIn(TemplateFieldIn):
    """A field for region-based template building: page of the sample and box location (recorded
    only; not part of the template definition)."""

    page: int = Field(default=1, ge=1)
    box: list[float] = Field(default=[], max_length=4)


class BoxesIn(BaseModel):
    """Region-based template building: sample document + selected fields. Any processed document can
    serve as a sample."""

    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[BoxFieldIn] = Field(min_length=1, max_length=80)


class SampleOut(BaseModel):
    template: TemplateOut
    document_id: uuid.UUID
    """The sample document has been reprocessed with the new template (a new run) and can be
    reviewed and exported directly."""


class TrialIn(BaseModel):
    document_id: uuid.UUID
    """Document to try: its stored OCR result is used; text is not re-recognized."""


class TrialField(BaseModel):
    name: str
    label: str
    value: str | None
    status: str
    """matched (evidence found), fuzzy (similar), not_found (no evidence), empty (absent from the
    source)."""
    note: Rendered = ""


class TrialIssue(BaseModel):
    rule: str
    title: Rendered
    message: Rendered
    severity: str


class TrialPage(BaseModel):
    page: int
    fields: list[TrialField]
    issues: list[TrialIssue]
    passed: list[str]


class TrialOut(BaseModel):
    """Trial run result: not saved; shows what this template version extracts from the document."""

    template: str
    version: int
    pages: list[TrialPage]
    duration_ms: int


TEMPLATE_FILE_FORMAT = "jacarandaparse-template"


class TemplateFile(BaseModel):
    """Exported or imported template file (JSON). The definition has the same format as .toml and
    the database."""

    format: Literal["jacarandaparse-template"] = TEMPLATE_FILE_FORMAT
    format_version: int = 1
    title: str = Field(min_length=1, max_length=100)
    titles: dict[str, str] = Field(default={}, max_length=30)
    """Template name in other languages."""
    language: str = Field(default="zh", max_length=16)
    """Primary language of title, description and every label and description."""
    descriptions: dict[str, str] = Field(default={}, max_length=30)
    """Template description in other languages."""
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    version: int = 1
    """Version at export time (numbering restarts at 1 after import)."""
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    rules: list[str] = []
    custom_rules: list[CustomRuleIO] = []
    domains: list[str] = []
    countries: list[str] | None = None
    """Absent (older template files, standard workbooks): inferred from the selected rules."""
    tags: list[str] = []
    entities: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    """Full data of the reference table (columns, rows)."""
    workbook: dict[str, Any] | None = None
    """Layout for export to the original workbook; the original workbook is in workbook_file
    (base64)."""
    workbook_file: str | None = None


# ---------- Creating templates from Excel ----------

ColumnSource = Literal["extract", "manual", "doc", "auto", "lookup", "none"]
SheetRoleName = Literal["document", "items", "entity", "reference", "options", "ignore"]


class ExcelColumn(BaseModel):
    """One column: header, first few values (preview), guessed type and source. Editable in the
    preview."""

    index: int
    header: str
    samples: list[str] = []
    source: ColumnSource = "extract"
    """extract from the document / manual entry / doc: a document-level field (repeated on every
    line-item row) / auto: filled by the system / lookup: from a reference table / none: left
    empty."""
    field: str = ""
    label: str = ""
    type: Literal["string", "number", "date", "date_range"] = "string"
    required: bool = False
    sensitive: bool = False
    description: str = ""
    aliases: list[str] = []
    options: list[str] = []
    auto: str = ""
    lookup: str = ""
    """"reference sheet name::column name"."""


class ExcelSheet(BaseModel):
    name: str
    role: SheetRoleName
    header_row: int
    rows: int = 0
    hidden: bool = False
    same_as: str = ""
    key_header: str = ""
    """Subject and reference tables: the identifier column; line-item tables: the column linking to
    the document."""
    match_field: str = ""
    """Reference tables: "sheet name::header" of the column whose values are matched."""
    reason: str = ""
    """Human-readable reason for the guess."""
    columns: list[ExcelColumn] = []


class ExcelAnalysisOut(BaseModel):
    """What the uploaded workbook is: a standard template workbook (the template is returned
    directly) or a business report (a guess for each sheet is returned)."""

    kind: Literal["standard", "report"]
    filename: str
    template: TemplateFile | None = None
    upload_id: uuid.UUID | None = None
    sheets: list[ExcelSheet] = []
    starter: str = ""
    starter_title: str = ""
    auto_columns: dict[str, str] = {}
    """Columns filled automatically by the system: auto value → display name."""
    ignored: list[str] = []
    """Standard template workbooks: notes on columns and rows that were not recognized and were
    therefore not imported."""


class ExcelBuildIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    starter: str = ""
    """Matching starter template: its rules whose fields are all present are carried over."""
    sheets: list[ExcelSheet] = Field(min_length=1, max_length=50)
