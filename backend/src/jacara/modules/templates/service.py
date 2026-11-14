"""Template business logic.

Three kinds of templates:
- My templates: in the database; can be created, edited (every save creates a new version) and
  archived; selected at upload.
- Starter library: starters/*.toml; "Add to my templates" makes a copy.
- Built-in templates: templates/*.toml, used by evaluation and the CLI; listed so that older
  documents can be filtered by template."""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from jacara.core.i18n import render, stored, tr
from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.core.storage import ObjectStorage, StorageError
from jacara.formulas import FormulaError, Scope, parse_condition, parse_formula
from jacara.models import Document, ExtractedValue, Extraction, TemplateRecord, TemplateVersion
from jacara.modules.audit.service import record
from jacara.modules.templates.repository import TemplateRepository
from jacara.modules.templates.schemas import (
    BoxesIn,
    CustomRuleIO,
    EntityOut,
    ExcelAnalysisOut,
    ExcelBuildIn,
    ExcelSheet,
    FormulaCheckIn,
    FormulaCheckOut,
    LabelsIn,
    ReferenceOut,
    RuleInfo,
    SampleIn,
    TemplateCreate,
    TemplateFieldIn,
    TemplateFieldOut,
    TemplateFile,
    TemplateIn,
    TemplateKind,
    TemplateOut,
    TemplateRuleOut,
    TemplateTableIn,
    TemplateTableOut,
    TemplateVersionOut,
    TrialField,
    TrialIssue,
    TrialOut,
    TrialPage,
    WorkbookColumnOut,
    WorkbookOut,
    WorkbookSheetOut,
)
from jacara.plugins.extractors.base import AUTO, DISCOVER
from jacara.plugins.validators import ALL_RULES, REGIONS
from jacara.plugins.validators.rules import RULES
from jacara.templates import (
    STARTERS,
    Template,
    TemplateError,
    TemplateField,
    clean_countries,
    clean_labels,
    from_definition,
    infer_countries,
    load_starter,
    load_template,
    template_names,
)

PipelineFactory = Callable[[], Any]
"""Pipeline for trial runs (only extraction, alignment and validation are used). Injected as a
dependency in the API and replaced by a fake in tests."""

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_EXCEL_MB = 20
"""Maximum size in MB of a workbook used to create a template."""


def _upload_key(upload_id: uuid.UUID) -> str:
    """Storage location of uploaded workbooks not yet confirmed as templates."""
    return f"template-uploads/{upload_id}.xlsx"


MAX_TRIAL_PAGES = 3
"""Maximum number of pages per trial run: trial runs are synchronous and long documents would take
too long."""


class TemplateFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


TemplateNotFound = TemplateFailure  # Legacy name.


def to_out(t: Template, kind: TemplateKind = "builtin") -> TemplateOut:
    return TemplateOut(
        name=t.name,
        version=t.version,
        title=t.title,
        titles=dict(t.titles),
        languages=list(t.languages),
        basis=list(t.basis),
        description=t.description,
        category=t.category,
        kind=kind,
        fields=[_field(f) for f in t.fields],
        rules=[
            TemplateRuleOut(
                id=r.id, title=render(r.title) or r.title, needs=list(r.needs), severity=r.severity
            )
            for r in t.rules
        ],
        custom_rules=[CustomRuleIO(**c.as_dict()) for c in t.custom_rules],
        domains=list(t.domains),
        countries=list(t.countries),
        tables=[
            TemplateTableOut(
                name=tb.name,
                label=tb.label,
                description=tb.description,
                columns=[_field(c) for c in tb.columns],
                required=tb.required,
                export=tb.export,
                names=dict(tb.names),
            )
            for tb in t.tables
        ],
        entities=[
            EntityOut(name=e.name, label=e.label, key=e.key, fields=list(e.fields))
            for e in t.entities
        ],
        references=[
            ReferenceOut(
                name=r.name,
                label=r.label,
                columns=list(r.columns),
                row_count=len(r.rows),
                match_field=r.match_field,
                match_column=r.match_column,
            )
            for r in t.references
        ],
        workbook=WorkbookOut(
            filename=t.workbook.filename,
            sheets=[
                WorkbookSheetOut(
                    name=sh.name,
                    role=sh.role,
                    header_row=sh.header_row,
                    same_as=sh.same_as,
                    table=sh.table,
                    entity=sh.entity,
                    reference=sh.reference,
                    columns=[
                        WorkbookColumnOut(index=c.index, header=c.header, ref=c.ref)
                        for c in sh.columns
                    ],
                )
                for sh in t.workbook.sheets
            ],
        )
        if t.workbook
        else None,
    )


def _field(f: TemplateField) -> TemplateFieldOut:
    return TemplateFieldOut(
        name=f.name,
        label=f.label,
        type=f.type,
        required=f.required,
        description=f.description,
        aliases=list(f.aliases),
        export=f.export,
        sensitive=f.sensitive,
        options=list(f.options),
        extract=f.extract,
        compute=f.compute,
        unique=f.unique,
        range_export=f.range_export,
        range_separator=f.range_separator,
        names=dict(f.names),
    )


def carry_over(
    definition: dict[str, Any],
    previous: dict[str, Any],
    column_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The editor does not change subjects, reference tables or the workbook layout on save: they
    are carried over from the previous version.

        References to fields deleted in the editor are removed from these places without error: a
    subject whose identifier field is gone is dropped; a reference table whose match field is
    gone is no longer matched; workbook columns filled by the field are left empty on export.
    column_refs: changes made in the editor to what a column of the original workbook is filled
    with ("sheet name::header" → field), applied before validation."""
    fields = {f["name"] for f in definition["fields"]}
    refs = fields | {f"{t['name']}.{c['name']}" for t in definition["tables"] for c in t["columns"]}
    # Start, end and number of days of a date range field can also fill a column of the original
    # workbook.
    refs |= {
        f"{f['name']}.{part}"
        for f in definition["fields"]
        if f.get("type") == "date_range"
        for part in ("start", "end", "days")
    }
    overrides = column_refs or {}
    tables = {t["name"] for t in definition["tables"]}
    entities = [
        {**e, "fields": [f for f in e["fields"] if f in fields]}
        for e in previous.get("entities") or []
        if e["key"] in fields
    ]
    references = [
        r if r.get("match_field") in refs else {**r, "match_field": "", "match_column": ""}
        for r in previous.get("references") or []
    ]
    names = {e["name"] for e in entities}
    workbook = previous.get("workbook")
    if workbook:
        ref_names = {r["name"]: set(r["columns"]) for r in references}

        def keep(ref: str) -> str:
            if not ref or ref.startswith("auto:"):
                return ref
            if ref.startswith("ref:"):
                name, _, column = ref[4:].partition(".")
                return ref if column in ref_names.get(name, set()) else ""
            return ref if ref in refs else ""

        sheets = []
        for sheet in workbook.get("sheets", []):
            sheet = {
                **sheet,
                "columns": [
                    {**c, "ref": keep(_override(overrides, sheet["name"], c))}
                    for c in sheet["columns"]
                ],
            }
            if (sheet["role"] == "items" and sheet.get("table") not in tables) or (
                sheet["role"] == "entity" and sheet.get("entity") not in names
            ):
                sheet = {**sheet, "role": "ignore", "columns": []}
            sheets.append(sheet)
        workbook = {**workbook, "sheets": sheets}
    return {**definition, "entities": entities, "references": references, "workbook": workbook}


def _override(overrides: dict[str, str], sheet: str, column: dict[str, Any]) -> str:
    """Use the editor's change for this column if any (empty = leave blank), otherwise the
    original."""
    key = f"{sheet}::{column['header']}"
    return overrides[key] if key in overrides else str(column.get("ref") or "")


def _field_dump(f: TemplateFieldIn) -> dict[str, Any]:
    """Store a field in the definition: date range export settings are written only for date range
    fields (they have no effect on others)."""
    data = f.model_dump()
    if f.type != "date_range":
        data.pop("range_export", None)
        data.pop("range_separator", None)
    if not data.get("names"):
        data.pop("names", None)
    return data


def runnable_rules(
    rules: list[str], definition: dict[str, Any], countries: list[str] | None
) -> list[str]:
    """Keep only built-in rules this template can run: all required fields present, and
    country-specific rules only for templates of that country. Rules depending on deleted fields
    or a changed country are dropped on save instead of being skipped forever. When countries is
    None (older template files without a country), no country filter is applied. Unknown rule ids
    are kept and reported clearly later."""
    from jacara.workbook.standard import usable_rule

    kept: list[str] = []
    for rule_id in dict.fromkeys(rules):
        rule = RULES.get(rule_id)
        # No country: treat the rule's own country as matching and filter by fields only.
        place = [c.upper() for c in countries] if countries is not None else [REGIONS.get(rule_id)]
        if rule is None or usable_rule(rule, {**definition, "countries": place}):
            kept.append(rule_id)
    return kept


def definition_of(data: TemplateIn) -> dict[str, Any]:
    """Editor submission → definition stored in the database (same structure as .toml)."""
    fields = [_field_dump(f) for f in data.fields]
    tables = [
        {**t.model_dump(), "columns": [_field_dump(c) for c in t.columns]} for t in data.tables
    ]
    return {
        "fields": fields,
        "tables": tables,
        "rules": runnable_rules(data.rules, {"fields": fields, "tables": tables}, data.countries),
        "custom_rules": [c.model_dump() for c in data.custom_rules],
        **({"titles": data.titles} if data.titles else {}),
    }


class Pick(NamedTuple):
    """Template selected for upload or reprocessing."""

    version_id: uuid.UUID | None
    """Version of my template; None for key-value discovery, automatic selection or the built-in
    receipt template."""
    discover: bool = False
    auto: bool = False

    @property
    def audit(self) -> str:
        """ "Template used" as written to the audit log."""
        if self.auto:
            return AUTO
        if self.discover:
            return DISCOVER
        return str(self.version_id or "receipt")


class TemplateService:
    def __init__(
        self,
        owner: Owner,
        session: Session | None = None,
        pipeline: PipelineFactory | None = None,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.owner = owner
        self.session = session
        self.pipeline = pipeline
        self.storage = storage
        self._usage: dict[uuid.UUID, tuple[int, datetime | None]] | None = None

    @property
    def repo(self) -> TemplateRepository:
        if self.session is None:
            raise RuntimeError(tr("这个操作要数据库会话"))
        return TemplateRepository(self.session)

    # ---- Read ----

    def list(self, builtin: bool = False) -> list[TemplateOut]:
        """My templates (non-archived first). With builtin=True, the built-in templates are added.

                Built-in templates (receipt, receipt_full) are only for evaluation and the CLI
        and are no longer listed in the web UI (since 2026-11-12); documents processed with them
        can still be viewed and reprocessed."""
        require(self.owner, "document:read")
        mine = (
            [self._mine_out(r) for r in self.repo.list(self.owner.team_id)] if self.session else []
        )
        if not builtin:
            return mine
        return mine + [to_out(load_template(name)) for name in template_names()]

    def starters(self) -> list[TemplateOut]:
        require(self.owner, "document:read")
        return [to_out(load_starter(name), "starter") for name in STARTERS]

    def formula_check(self, data: FormulaCheckIn) -> FormulaCheckOut:
        """Validate a formula or rule condition from the editor."""
        require(self.owner, "document:read")
        fields = {f.name: f.type for f in data.fields}
        tables = {t.name: {c.name: c.type for c in t.columns} for t in data.tables}
        labels = {f.name: f.label for f in data.fields}
        for t in data.tables:
            labels[t.name] = t.label
            labels |= {f"{t.name}.{c.name}": c.label for c in t.columns}
        if data.row is not None and data.row not in tables:
            return FormulaCheckOut(ok=False, message=tr("没有叫 {row} 的表", row=data.row))
        if data.row is not None:
            columns = next(t.columns for t in data.tables if t.name == data.row)
            labels |= {c.name: c.label for c in columns}
            # Table column formulas may use columns of the same row and regular fields; per-row
            # rules use columns only.
            scope = Scope(fields if data.kind == "formula" else {}, tables, tables[data.row])
        else:
            scope = Scope(fields, tables)
        try:
            if data.kind == "formula":
                readable = parse_formula(data.text, scope).readable(labels)
            else:
                readable = parse_condition(data.text, scope).readable(labels)
        except FormulaError as err:
            return FormulaCheckOut(ok=False, message=render(str(err)))
        return FormulaCheckOut(ok=True, readable=render(readable))

    def rules(self) -> list[RuleInfo]:
        """All rules selectable in the editor."""
        require(self.owner, "document:read")
        return [
            RuleInfo(
                id=r.id,
                title=tr(r.title),
                needs=list(r.needs),
                severity=r.severity,
                per_row=r.per_row,
                requires=list(r.requires),
                country=REGIONS.get(r.id, ""),
            )
            for r in ALL_RULES
        ]

    def get(self, name: str) -> TemplateOut:
        require(self.owner, "document:read")
        template_id = _uuid(name)
        if template_id is not None and self.session is not None:
            found = self.repo.get(template_id, self.owner.team_id)
            if found is None:
                raise TemplateFailure("not_found", tr("找不到这个模板"))
            out = self._mine_out(found)
            return out.model_copy(
                update={
                    "versions": [
                        TemplateVersionOut(version=v.version, note=v.note, created_at=v.created_at)
                        for v in reversed(found.versions)
                    ],
                    "used": self.repo.usage(found.id),
                }
            )
        if name in STARTERS:
            return to_out(load_starter(name), "starter")
        try:
            return to_out(load_template(name))
        except TemplateError as err:
            raise TemplateFailure("not_found", str(err)) from err

    def version(self, template_id: uuid.UUID, version: int) -> TemplateOut:
        """An older version of my template (read-only)."""
        require(self.owner, "document:read")
        found = self._find(template_id)
        match = next((v for v in found.versions if v.version == version), None)
        if match is None:
            raise TemplateFailure("not_found", tr("这个模板没有第 {version} 版", version=version))
        return self._out(found, match)

    # ---- Create, save, archive ----

    def create(self, data: TemplateCreate) -> TemplateOut:
        require(self.owner, "template:manage")
        if data.source == "starter":
            if data.starter not in STARTERS:
                raise TemplateFailure(
                    "invalid", tr("没有叫 {starter} 的起步模板", starter=data.starter)
                )
            base = load_starter(data.starter)
            source = f"starter:{data.starter}"
            title = data.title or base.title
            category, description, definition = base.category, base.description, base.definition()
            labels = (list(base.domains), list(base.countries), [])
        elif data.source == "copy":
            if data.copy_of is None:
                raise TemplateFailure("invalid", tr("请选择要复制的模板"))
            original = self._find(data.copy_of)
            latest = self.repo.latest(original)
            source = f"copy:{original.id}"
            title = data.title or tr("{title}（副本）", title=original.title)
            category, description = original.category, original.description
            definition = latest.definition
            labels = (list(original.domains), list(original.countries), list(original.tags))
        elif data.content is not None:
            # Blank template: created only when saved from the editor.
            content = data.content
            source, title = "blank", content.title.strip()
            category, description = content.category.strip(), content.description.strip()
            definition = definition_of(content)
            labels = (content.domains, content.countries, content.tags)
        else:
            raise TemplateFailure("invalid", tr("空白模板要先填好内容再保存"))
        found = self._new(
            title, category, description, source, definition, stored("新建"), labels=labels
        )
        return self._mine_out(found)

    def save(self, template_id: uuid.UUID, data: TemplateIn) -> TemplateOut:
        """Save a new version. Documents that used older versions are unaffected; future uploads and
        reprocessing use the new version."""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        latest = self.repo.latest(found)
        definition = carry_over(definition_of(data), latest.definition, data.column_refs)
        # Only name, description or tags changed (definition unchanged): no new version, and
        # documents that used it need no reprocessing.
        changed = definition != latest.definition
        version = found.latest_version + 1 if changed else found.latest_version
        self._check(definition, data.title, version)
        if changed:
            self.repo.add_version(
                TemplateVersion(
                    template_id=found.id,
                    version=version,
                    definition=definition,
                    note=data.note.strip(),
                    user_id=self.owner.user_id,
                    team_id=found.team_id,
                )
            )
        found.latest_version = version
        found.title = data.title.strip()
        found.description = data.description.strip()
        self._set_labels(found, data.domains or [data.category], data.countries, data.tags)
        self._audit("template:save", found, version=version, new_version=changed)
        self._reload(found)
        return self._mine_out(found)

    def labels(self, template_id: uuid.UUID, data: LabelsIn) -> TemplateOut:
        """Change only domains, country or region, and custom tags (no new version)."""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        self._set_labels(
            found,
            found.domains if data.domains is None else data.domains,
            found.countries if data.countries is None else data.countries,
            found.tags if data.tags is None else data.tags,
        )
        self._audit(
            "template:labels",
            found,
            domains=found.domains,
            countries=found.countries,
            tags=found.tags,
        )
        self._reload(found)
        return self._mine_out(found)

    def _set_labels(
        self,
        found: TemplateRecord,
        domains: list[str],
        countries: list[str],
        tags: list[str],
    ) -> None:
        where = tr("模板「{title}」", title=found.title)
        try:
            found.domains = list(clean_labels(domains, where, tr("功能领域"), 10))
            found.countries = list(clean_countries(countries, where))
            found.tags = list(clean_labels(tags, where, tr("自定义标签"), 20))
        except TemplateError as err:
            raise TemplateFailure("invalid", str(err)) from err
        found.category = found.domains[0] if found.domains else ""

    def archive(self, template_id: uuid.UUID, archived: bool) -> TemplateOut:
        require(self.owner, "template:manage")
        found = self._find(template_id)
        found.archived = archived
        self._audit("template:archive" if archived else "template:restore", found)
        return self._mine_out(found)

    # ---- Export, import, delete ----

    def _template_named(self, name: str) -> Template:
        """My template (latest version), a starter template or a built-in template."""
        require(self.owner, "document:read")
        template_id = _uuid(name)
        if template_id is not None:
            found = self._find(template_id)
            return self._template(found, self.repo.latest(found))
        if name in STARTERS:
            return load_starter(name)
        try:
            return load_template(name)
        except TemplateError as err:
            raise TemplateFailure("not_found", str(err)) from err

    def export_file(self, name: str) -> TemplateFile:
        """Template → JSON for import on another machine: the complete content, including the
        original workbook (for original-sheet export)."""
        template = self._template_named(name)
        template_id = _uuid(name)
        record = self._find(template_id) if template_id is not None else None
        definition = template.definition()
        workbook_file = None
        if template.workbook and self.storage is not None:
            try:
                workbook_file = base64.b64encode(self.storage.get(template.workbook.file)).decode()
            except StorageError:
                workbook_file = None
        return TemplateFile(
            title=template.title,
            category=template.category,
            description=template.description,
            version=template.version,
            fields=[TemplateFieldIn(**f) for f in definition["fields"]],
            tables=[TemplateTableIn(**t) for t in definition["tables"]],
            rules=definition["rules"],
            custom_rules=[CustomRuleIO(**c) for c in definition.get("custom_rules", [])],
            domains=list(template.domains),
            countries=list(template.countries),
            tags=list(record.tags or ()) if record is not None else [],
            entities=definition["entities"],
            references=definition["references"],
            workbook=definition["workbook"] if workbook_file else None,
            workbook_file=workbook_file,
        )

    def export_excel(self, name: str) -> tuple[str, bytes]:
        """Template → standard template workbook: one sheet each for fields, tables, rules, subjects
        and reference tables."""
        from jacara.workbook.standard import to_excel

        data = self.export_file(name)
        return f"{data.title}.xlsx", to_excel(data.model_dump())

    def import_file(self, data: TemplateFile) -> TemplateOut:
        """Import a template file as a new template of mine (version 1). Unknown rule ids and
        invalid fields are reported precisely."""
        require(self.owner, "template:manage")
        workbook = data.workbook
        if workbook and data.workbook_file:
            key = self._store_workbook(base64.b64decode(data.workbook_file))
            workbook = {**workbook, "file": key}
        else:
            workbook = None
        definition = {
            "fields": [f.model_dump() for f in data.fields],
            "tables": [t.model_dump() for t in data.tables],
            "rules": runnable_rules(
                data.rules,
                {
                    "fields": [f.model_dump() for f in data.fields],
                    "tables": [t.model_dump() for t in data.tables],
                },
                data.countries,
            ),
            "custom_rules": [c.model_dump() for c in data.custom_rules],
            "entities": data.entities,
            "references": data.references,
            "workbook": workbook,
        }
        found = self._new(
            data.title.strip(),
            data.category.strip(),
            data.description.strip(),
            "import",
            definition,
            stored("导入（导出时是第 {version} 版）", version=data.version),
            labels=(
                data.domains or ([data.category.strip()] if data.category.strip() else []),
                # Older template files and standard workbooks have no country: infer it from the
                # selected rules.
                list(infer_countries(data.rules)) if data.countries is None else data.countries,
                data.tags,
            ),
        )
        return self._mine_out(found)

    # ---- Creating templates from Excel ----

    def analyze_excel(self, filename: str, data: bytes) -> ExcelAnalysisOut:
        """Upload a workbook: a standard template workbook is read directly; a business report is
        stored first and a guess for each sheet is returned."""
        require(self.owner, "template:manage")
        from jacara.workbook.analyze import WorkbookError
        from jacara.workbook.standard import StandardError, from_excel, is_standard

        if not data:
            raise TemplateFailure("invalid", tr("文件是空的"))
        if len(data) > MAX_EXCEL_MB * 1024 * 1024:
            raise TemplateFailure("invalid", tr("Excel 超过 {limit} MB", limit=MAX_EXCEL_MB))
        if is_standard(data):
            try:
                template = TemplateFile.model_validate(from_excel(data))
            except (StandardError, ValueError) as err:
                raise TemplateFailure("invalid", str(err)) from err
            return ExcelAnalysisOut(kind="standard", filename=filename, template=template)
        upload_id = uuid.uuid4()
        try:
            out = self._analysis(filename, data, upload_id)
        except WorkbookError as err:
            raise TemplateFailure("invalid", str(err)) from err
        try:
            self._storage().put(_upload_key(upload_id), data, XLSX)
            self._storage().put(_upload_key(upload_id) + ".name", filename.encode(), "text/plain")
        except StorageError as err:
            raise TemplateFailure("unavailable", tr("对象存储暂时不可用，请稍后再试")) from err
        return out

    def reanalyze_excel(self, upload_id: uuid.UUID) -> ExcelAnalysisOut:
        """Show a previously uploaded workbook again (when the preview page is reloaded)."""
        require(self.owner, "template:manage")
        data, filename = self._upload(upload_id)
        return self._analysis(filename, data, upload_id)

    def build_excel(self, upload_id: uuid.UUID, choice: ExcelBuildIn) -> TemplateOut:
        """Create a template from the confirmed preview; the original workbook is stored for later
        export into it."""
        require(self.owner, "template:manage")
        from jacara.workbook.analyze import WorkbookError
        from jacara.workbook.build import BuildError, ColumnChoice, SheetChoice, build_definition

        data, filename = self._upload(upload_id)
        sheets = [
            SheetChoice(
                name=s.name,
                role=s.role,
                header_row=s.header_row,
                same_as=s.same_as,
                key_header=s.key_header,
                match_field=s.match_field,
                columns=[ColumnChoice(**c.model_dump(exclude={"samples"})) for c in s.columns],
            )
            for s in choice.sheets
        ]
        if choice.starter and choice.starter not in STARTERS:
            raise TemplateFailure(
                "invalid", tr("没有叫 {starter} 的起步模板", starter=choice.starter)
            )
        key = self._store_workbook(data)
        try:
            definition = build_definition(data, filename, key, sheets, choice.starter)
        except (BuildError, WorkbookError) as err:
            raise TemplateFailure("invalid", str(err)) from err
        found = self._new(
            choice.title.strip(),
            choice.category.strip(),
            choice.description.strip(),
            "excel",
            definition,
            stored("从 Excel 建：{filename}", filename=filename),
        )
        return self._mine_out(found)

    def _analysis(self, filename: str, data: bytes, upload_id: uuid.UUID) -> ExcelAnalysisOut:
        from dataclasses import asdict

        from jacara.templates.extras import AUTO_COLUMNS
        from jacara.workbook.analyze import analyze

        result = analyze(data, filename)
        return ExcelAnalysisOut(
            kind="report",
            filename=filename,
            upload_id=upload_id,
            sheets=[ExcelSheet.model_validate(asdict(s)) for s in result.sheets],
            starter=result.starter,
            starter_title=result.starter_title,
            auto_columns={key: tr(name) for key, name in AUTO_COLUMNS.items()},
        )

    def _upload(self, upload_id: uuid.UUID) -> tuple[bytes, str]:
        try:
            data = self._storage().get(_upload_key(upload_id))
            name = self._storage().get(_upload_key(upload_id) + ".name").decode()
        except StorageError as err:
            raise TemplateFailure("not_found", tr("找不到上传的 Excel，请重新上传")) from err
        return data, name

    def _store_workbook(self, data: bytes) -> str:
        key = f"templates/files/{uuid.uuid4()}.xlsx"
        try:
            self._storage().put(key, data, XLSX)
        except StorageError as err:
            raise TemplateFailure("unavailable", tr("对象存储暂时不可用，请稍后再试")) from err
        return key

    def _storage(self) -> ObjectStorage:
        if self.storage is None:
            raise TemplateFailure("unavailable", tr("对象存储没有准备好"))
        return self.storage

    def delete(self, template_id: uuid.UUID) -> int:
        """Delete a template (archived and used templates included). Returns the number of documents
        that used it.

                Unused: the template and all versions are deleted. Used (processed or queued):
        removed from the template list, upload and reprocessing, but the versions stay in the
        database, so those documents still show field names, check rules and export normally
        (they record the version they used)."""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        used = self.repo.documents_using(found.id)
        self._audit("template:delete", found, versions=found.latest_version, documents=used)
        if used:
            found.deleted_at = datetime.now(UTC)
            found.archived = True
        else:
            self.repo.delete(found)
        return used

    # ---- Template selection at upload ----

    def pick_for_upload(self, choice: str | None) -> Pick:
        """Template selected at upload → (template version id, key-value discovery, automatic
        selection).

                Not selected: the template this user used for the last upload; for the first
        upload, the built-in receipt template. auto: each document picks one of my templates
        automatically."""
        if choice == DISCOVER:
            return Pick(None, discover=True)
        if choice == AUTO:
            return Pick(None, auto=True)
        if choice in (None, ""):
            return self._last_choice()
        template_id = _uuid(choice)
        if template_id is None:
            raise TemplateFailure("invalid", tr('请选择我的模板，或者"抽取所有键值对"'))
        found = self._find(template_id)
        if found.archived:
            raise TemplateFailure(
                "invalid", tr("模板「{title}」已经归档了，不能再用来上传", title=found.title)
            )
        return Pick(self.repo.latest(found).id)

    def latest_version_id(self, template_id: uuid.UUID) -> uuid.UUID:
        found = self.repo.get(template_id, self.owner.team_id)
        if found is None:
            raise TemplateFailure(
                "invalid", tr("这份文档用的模板已经删除了，请选一个模板再重新处理")
            )
        return self.repo.latest(found).id

    def _last_choice(self) -> Pick:
        from sqlalchemy import select

        last = self.session_or_fail().scalar(
            select(Document)
            .where(Document.user_id == self.owner.user_id, Document.team_id == self.owner.team_id)
            .order_by(Document.created_at.desc())
            .limit(1)
        )
        if last is None:
            return Pick(None)
        if last.auto:
            return Pick(None, auto=True)
        if last.discover:
            return Pick(None, discover=True)
        if last.template_version_id is None:
            return Pick(None)
        version = self.session_or_fail().get_one(TemplateVersion, last.template_version_id)
        record_ = version.template
        if record_.archived or record_.deleted_at is not None:
            return Pick(None)
        return Pick(self.repo.latest(record_).id)

    # ---- Trial runs ----

    def trial(self, template_id: uuid.UUID, document_id: uuid.UUID) -> TrialOut:
        """Trial-run the latest version of this template on a processed document without saving or
        changing the document."""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        template = self._template(found, self.repo.latest(found))
        document = self._document(document_id)
        pages = [e for e in document.extractions if e.ocr is not None][:MAX_TRIAL_PAGES]
        if not pages:
            raise TemplateFailure(
                "invalid", tr("这份文档没有存 OCR 结果（更新前处理的），请先重新处理一次再试跑")
            )
        if self.pipeline is None:
            raise TemplateFailure("unavailable", tr("模型服务没有准备好"))
        from jacara.pipeline.pages import pages_json, stack_pages
        from jacara.pipeline.store import page_from_json
        from jacara.structure.graph import structure_from_json

        pipeline = self.pipeline()
        started = time.monotonic()
        out: list[TrialPage] = []
        for extraction in pages:
            # Documents spanning several pages: restore each page and combine them as during
            # processing.
            stacked = stack_pages(
                [
                    page_from_json(p, p.get("engine") or extraction.ocr_engine)
                    for _, p in pages_json(extraction.ocr, extraction.page)
                ]
            )
            page = stacked.page
            # Use the page structure stored during processing (layout regions and tables from
            # models); otherwise re-analyze by rules.
            structure = (
                structure_from_json(extraction.structure, page) if extraction.structure else None
            )
            report = pipeline.extract_page(
                page, document.filename, extraction.page, template, structure=structure
            )
            labels = template.labels()
            from jacara.pipeline.current import label_for

            out.append(
                TrialPage(
                    page=extraction.page,
                    fields=[
                        TrialField(
                            name=a.field.name,
                            label=label_for(labels, a.field.name) or a.field.name,
                            value=None if a.field.value is None else str(a.field.value),
                            status=a.status,
                            note=a.note,
                        )
                        for a in report.aligned
                    ],
                    issues=[
                        TrialIssue(
                            rule=i.rule, title=i.title, message=i.message, severity=i.severity
                        )
                        for i in report.checks.issues
                    ],
                    passed=list(report.checks.passed),
                )
            )
        return TrialOut(
            template=template.title,
            version=template.version,
            pages=out,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # ---- Creating templates from samples ----

    def from_sample(self, data: SampleIn) -> tuple[TemplateOut, uuid.UUID]:
        """Sample document (with extracted key-value pairs) → version 1 of a new template; the
        sample is reprocessed with the new template (a new run).

                Selected key-value pairs are carried over as is (values, evidence and manual
        corrections), without running the model again; added fields are empty until filled.
        Labels on the document are recorded as aliases so the model recognizes them more easily."""
        require(self.owner, "template:manage")
        document = self._document(data.document_id)
        current = document.extractions
        if not current or any(e.template != DISCOVER for e in current):
            raise TemplateFailure("invalid", tr('这份文档不是"抽取所有键值对"处理的，不能当样例'))
        sources = {v.name: v for v in current[0].values}
        fields: list[dict[str, Any]] = []
        for f in data.fields:
            item = f.model_dump(exclude={"source"})
            original = sources.get(f.source) if f.source else None
            if f.source and original is None:
                raise TemplateFailure("invalid", tr("样例里没有 {source}", source=f.source))
            if original is not None and original.label and original.label != f.label:
                item["aliases"] = list(dict.fromkeys([*f.aliases, original.label]))
            fields.append(item)
        rules = runnable_rules(data.rules, {"fields": fields, "tables": []}, None)
        definition = {"fields": fields, "tables": [], "rules": rules}
        found = self._new(
            data.title.strip(),
            data.category.strip(),
            data.description.strip(),
            f"sample:{document.id}",
            definition,
            stored("用样例建模板"),
        )
        version = self.repo.latest(found)
        template = self._template(found, version)
        self._apply_sample(document, version, template, data)
        return self._mine_out(found), document.id

    def from_boxes(self, data: BoxesIn) -> TemplateOut:
        """Fields selected as regions on a sample → version 1 of a new template. Name, type, aliases
        (the nearby label) and description (location on the document) are filled in on the page;
        box positions are recorded only in the template origin, not in the definition. The sample
        is not reprocessed automatically; the page offers "reprocess with the new template"."""
        require(self.owner, "template:manage")
        document = self._document(data.document_id)
        fields = [f.model_dump(exclude={"page", "box"}) for f in data.fields]
        definition = {"fields": fields, "tables": [], "rules": []}
        found = self._new(
            data.title.strip(),
            data.category.strip(),
            data.description.strip(),
            f"boxes:{document.id}",
            definition,
            stored("在样例上框选建模板"),
        )
        return self._mine_out(found)

    def _apply_sample(
        self, document: Document, version: TemplateVersion, template: Template, data: SampleIn
    ) -> None:
        from jacara.pipeline.process import next_run
        from jacara.pipeline.store import checks_json
        from jacara.plugins.extractors.base import ExtractedField

        session = self.session_or_fail()
        run = next_run(session, document)
        for old in document.extractions:
            by_label = {v.label: v for v in old.values if v.label}
            first = {v.name: v for v in document.extractions[0].values}
            new = Extraction(
                document=document,
                page=old.page,
                last_page=old.last_page,
                source=old.source,
                ocr_engine=old.ocr_engine,
                model=old.model,
                backend=old.backend,
                prompt_version=old.prompt_version,
                template=template.name,
                template_version=template.version,
                template_version_id=version.id,
                run=run,
                run_reason=stored("用样例建模板：选中的键值对搬过来，不重新抽取"),
                duration_ms=0,
                raw_output=old.raw_output,
                checks={},
                ocr=old.ocr,
                structure=old.structure,
                user_id=document.user_id,
                team_id=document.team_id,
            )
            session.add(new)
            copied: list[ExtractedValue] = []
            for position, f in enumerate(data.fields):
                picked = first.get(f.source) if f.source else None
                # On the first page, match by field name; on other pages, match the same item by its
                # label on the document.
                source = (
                    picked
                    if old is document.extractions[0]
                    else by_label.get(picked.label)
                    if picked is not None and picked.label
                    else None
                )
                copied.append(self._copy_value(new, source, f.name, f.type, position))
            result = template.validator().validate(
                [
                    ExtractedField(v.name, f.type, _typed(f.type, _current(v)))
                    for v, f in zip(copied, data.fields, strict=True)
                ]
            )
            new.checks = checks_json(result)
        document.current_run = run
        document.template_version_id = version.id
        document.discover = False
        document.auto = False
        document.reviewed_at = None
        if document.status == "reviewed":
            document.status = "done"
        session.flush()
        session.expire(document, ["extractions"])

    def _copy_value(
        self,
        extraction: Extraction,
        source: ExtractedValue | None,
        name: str,
        type_: str,
        position: int,
    ) -> ExtractedValue:
        from jacara.models import ValueRevision

        session = self.session_or_fail()
        who = {"user_id": extraction.user_id, "team_id": extraction.team_id}
        if source is None:
            value = ExtractedValue(
                extraction=extraction,
                position=position,
                sort_key=float(position),
                name=name,
                type=type_,
                value=None,
                status="empty",
                needs_review=False,
                note="",
                evidence=None,
                origin="manual",
                **who,
            )
            session.add(value)
            return value
        value = ExtractedValue(
            extraction=extraction,
            position=position,
            sort_key=float(position),
            name=name,
            type=type_,
            value=source.value,
            row_no=source.row_no,
            status=source.status,
            needs_review=source.needs_review,
            note=source.note,
            evidence=source.evidence,
            origin=source.origin,
            **who,
        )
        session.add(value)
        for r in source.revisions:
            session.add(
                ValueRevision(
                    original=value,
                    seq=r.seq,
                    action=r.action,
                    new_value=r.new_value,
                    reason=r.reason,
                    evidence=r.evidence,
                    user_id=r.user_id,
                    team_id=r.team_id,
                )
            )
        return value

    # ---- Internal ----

    def session_or_fail(self) -> Session:
        if self.session is None:
            raise RuntimeError(tr("这个操作要数据库会话"))
        return self.session

    def _new(
        self,
        title: str,
        category: str,
        description: str,
        source: str,
        definition: dict[str, Any],
        note: str,
        labels: tuple[list[str], list[str], list[str]] | None = None,
    ) -> TemplateRecord:
        """Create a template (version 1). labels is (domains, country or region, custom tags); when
        omitted, derived from category and the rules selected in the definition (templates
        created from Excel or samples)."""
        template = self._check(definition, title, 1)
        domains, countries, tags = labels or (
            [category] if category else [],
            list(template.countries),
            [],
        )
        found = self.repo.add(
            TemplateRecord(
                title=title,
                category=category,
                description=description,
                source=source,
                latest_version=1,
                user_id=self.owner.user_id,
                team_id=self.owner.team_id,
            )
        )
        self.repo.add_version(
            TemplateVersion(
                template_id=found.id,
                version=1,
                definition=definition,
                note=note,
                user_id=self.owner.user_id,
                team_id=self.owner.team_id,
            )
        )
        self._set_labels(found, domains, countries, tags)
        self._reload(found)
        self._audit("template:create", found, source=source)
        return found

    def _reload(self, found: TemplateRecord) -> None:
        """Flush before reloading the version list (refresh does not flush and would overwrite
        pending changes with stored values)."""
        session = self.session_or_fail()
        session.flush()
        session.expire(found, ["versions"])

    def _check(self, definition: dict[str, Any], title: str, version: int) -> Template:
        try:
            return from_definition(definition, "check", version, title)
        except TemplateError as err:
            raise TemplateFailure("invalid", str(err)) from err

    def _find(self, template_id: uuid.UUID) -> TemplateRecord:
        found = self.repo.get(template_id, self.owner.team_id)
        if found is None:
            raise TemplateFailure("not_found", tr("找不到这个模板"))
        return found

    def _document(self, document_id: uuid.UUID) -> Document:
        from jacara.modules.documents.repository import DocumentRepository

        document = DocumentRepository(self.session_or_fail()).get(document_id, self.owner.team_id)
        if document is None:
            raise TemplateFailure("not_found", tr("找不到这个文档"))
        if document.status not in ("done", "reviewed"):
            raise TemplateFailure("conflict", tr("这份文档还没处理完"))
        return document

    def _template(self, found: TemplateRecord, version: TemplateVersion) -> Template:
        template = from_definition(
            version.definition,
            str(found.id),
            version.version,
            found.title,
            found.category,
            found.description,
        )
        # Tags recorded in the templates table take precedence.
        return replace(
            template, domains=tuple(found.domains or ()), countries=tuple(found.countries or ())
        )

    def _out(self, found: TemplateRecord, version: TemplateVersion) -> TemplateOut:
        template = self._template(found, version)
        used, last = self._stats().get(found.id, (0, None))
        return to_out(template, "mine").model_copy(
            update={
                "id": found.id,
                "archived": found.archived,
                "source": found.source,
                "updated_at": found.updated_at,
                "starter_formulas": _starter_formulas(template, found.source),
                "tags": list(found.tags or ()),
                "used": used,
                "last_used_at": last,
            }
        )

    def _stats(self) -> dict[uuid.UUID, tuple[int, datetime | None]]:
        """Document count and last use per template (queried once per request)."""
        if self._usage is None:
            self._usage = self.repo.usage_stats(self.owner.team_id)
        return self._usage

    def _mine_out(self, found: TemplateRecord) -> TemplateOut:
        return self._out(found, self.repo.latest(found))

    def _audit(self, action: str, found: TemplateRecord, **details: Any) -> None:
        record(
            self.session_or_fail(),
            self.owner,
            action,
            "template",
            found.id,
            title=found.title,
            **details,
        )


def _starter_formulas(template: Template, source: str) -> dict[str, str]:
    """Formulas added to the starter template later: same field ID and type, but no formula in this
    copy."""
    name = source.removeprefix("starter:")
    if not source.startswith("starter:") or name not in STARTERS:
        return {}
    starter = load_starter(name)
    mine = {f.name: f for f in template.fields}
    mine |= {f"{t.name}.{c.name}": c for t in template.tables for c in t.columns}
    theirs = {f.name: f for f in starter.fields}
    theirs |= {f"{t.name}.{c.name}": c for t in starter.tables for c in t.columns}
    return {
        key: f.compute
        for key, f in theirs.items()
        if f.compute and key in mine and not mine[key].compute and mine[key].type == f.type
    }


def _uuid(text: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def _current(value: ExtractedValue) -> str | None:
    return value.revisions[-1].new_value if value.revisions else value.value


def _typed(type_: str, raw: str | None) -> str | float | None:
    from jacara.pipeline.current import typed_value

    return typed_value(type_, raw)
