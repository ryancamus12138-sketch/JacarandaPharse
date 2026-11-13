"""模板的业务逻辑（ADR 0030）。

三种模板：
- 我的模板：数据库里，可以新建、编辑（每次保存一个新版本）、归档；上传时选它。
- 起步模板库：starters/*.toml，"添加到我的模板"就是复制一份。
- 内置模板：templates/*.toml，评测和命令行用；列表里能看到，方便按模板筛选老文档。
"""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

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
"""试跑要用的流水线（只用到抽取、对齐、校验）。接口里通过依赖注入，测试时换成假的。"""

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_EXCEL_MB = 20
"""建模板用的 Excel 最大多少 MB。"""


def _upload_key(upload_id: uuid.UUID) -> str:
    """上传了、还没确认建模板的 Excel 存在哪。"""
    return f"template-uploads/{upload_id}.xlsx"


MAX_TRIAL_PAGES = 3
"""试跑最多跑几页：试跑是同步的，等着看结果，页数多了要等太久。"""


class TemplateFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


TemplateNotFound = TemplateFailure  # 兼容旧名字


def to_out(t: Template, kind: TemplateKind = "builtin") -> TemplateOut:
    return TemplateOut(
        name=t.name,
        version=t.version,
        title=t.title,
        titles=dict(t.titles),
        languages=list(t.languages),
        description=t.description,
        category=t.category,
        kind=kind,
        fields=[_field(f) for f in t.fields],
        rules=[
            TemplateRuleOut(id=r.id, title=r.title, needs=list(r.needs), severity=r.severity)
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
    """编辑器保存时不改对象、参照表、工作簿样式：从上一版带过来。

    编辑器里删掉的字段，它们在这几处的引用跟着去掉（不报错）：
    对象的编号字段没了，这个对象就不要了；参照表的对照字段没了，只是不再对照；
    工作簿里填这个字段的列，导出时留空。
    column_refs：编辑器里改了原表某一列填什么（"sheet 名::表头" → 字段），先改再检查。
    """
    fields = {f["name"] for f in definition["fields"]}
    refs = fields | {f"{t['name']}.{c['name']}" for t in definition["tables"] for c in t["columns"]}
    # 日期范围字段的开始、结束、天数也能填进原表的一列（ADR 0039）
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
    """编辑器改了这一列填什么就用改的（改成空 = 留空），没改用原来的。"""
    key = f"{sheet}::{column['header']}"
    return overrides[key] if key in overrides else str(column.get("ref") or "")


def _field_dump(f: TemplateFieldIn) -> dict[str, Any]:
    """字段存进定义：日期范围的导出设置只有日期范围字段才写（别的字段写了也没用）。"""
    data = f.model_dump()
    if f.type != "date_range":
        data.pop("range_export", None)
        data.pop("range_separator", None)
    if not data.get("names"):
        data.pop("names", None)
    return data


def definition_of(data: TemplateIn) -> dict[str, Any]:
    """编辑器提交的内容 → 存进数据库的定义（和 .toml 一样结构）。"""
    return {
        "fields": [_field_dump(f) for f in data.fields],
        "tables": [
            {**t.model_dump(), "columns": [_field_dump(c) for c in t.columns]} for t in data.tables
        ],
        "rules": list(dict.fromkeys(data.rules)),
        "custom_rules": [c.model_dump() for c in data.custom_rules],
        **({"titles": data.titles} if data.titles else {}),
    }


class Pick(NamedTuple):
    """上传、重新处理时选的模板。"""

    version_id: uuid.UUID | None
    """我的模板的哪个版本；None：键值对发现、自动判断，或者内置的收据模板。"""
    discover: bool = False
    auto: bool = False

    @property
    def audit(self) -> str:
        """审计日志里写的"用了什么模板"。"""
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
            raise RuntimeError("这个操作要数据库会话")
        return TemplateRepository(self.session)

    # ---- 查看 ----

    def list(self, builtin: bool = False) -> list[TemplateOut]:
        """我的模板（没归档的在前）。builtin=True 时再加上内置模板。

        内置模板（receipt、receipt_full）只给评测和命令行用，网页上不再列出（2026-11-12）；
        以前用它处理的文档照常能看、能重新处理。
        """
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
        """检查编辑区里的一条公式或规则条件（ADR 0035）。"""
        require(self.owner, "document:read")
        fields = {f.name: f.type for f in data.fields}
        tables = {t.name: {c.name: c.type for c in t.columns} for t in data.tables}
        labels = {f.name: f.label for f in data.fields}
        for t in data.tables:
            labels[t.name] = t.label
            labels |= {f"{t.name}.{c.name}": c.label for c in t.columns}
        if data.row is not None and data.row not in tables:
            return FormulaCheckOut(ok=False, message=f"没有叫 {data.row} 的表")
        if data.row is not None:
            columns = next(t.columns for t in data.tables if t.name == data.row)
            labels |= {c.name: c.label for c in columns}
            # 表格的列的公式可以用同一行的列和普通字段；"每一行"的规则只用列
            scope = Scope(fields if data.kind == "formula" else {}, tables, tables[data.row])
        else:
            scope = Scope(fields, tables)
        try:
            if data.kind == "formula":
                readable = parse_formula(data.text, scope).readable(labels)
            else:
                readable = parse_condition(data.text, scope).readable(labels)
        except FormulaError as err:
            return FormulaCheckOut(ok=False, message=str(err))
        return FormulaCheckOut(ok=True, readable=readable)

    def rules(self) -> list[RuleInfo]:
        """编辑器里能选的所有规则。"""
        require(self.owner, "document:read")
        return [
            RuleInfo(
                id=r.id,
                title=r.title,
                needs=list(r.needs),
                severity=r.severity,
                per_row=r.per_row,
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
                raise TemplateFailure("not_found", "找不到这个模板")
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
        """我的模板的某个旧版本（只读）。"""
        require(self.owner, "document:read")
        found = self._find(template_id)
        match = next((v for v in found.versions if v.version == version), None)
        if match is None:
            raise TemplateFailure("not_found", f"这个模板没有第 {version} 版")
        return self._out(found, match)

    # ---- 新建、保存、归档 ----

    def create(self, data: TemplateCreate) -> TemplateOut:
        require(self.owner, "template:manage")
        if data.source == "starter":
            if data.starter not in STARTERS:
                raise TemplateFailure("invalid", f"没有叫 {data.starter} 的起步模板")
            base = load_starter(data.starter)
            source = f"starter:{data.starter}"
            title = data.title or base.title
            category, description, definition = base.category, base.description, base.definition()
            labels = (list(base.domains), list(base.countries), [])
        elif data.source == "copy":
            if data.copy_of is None:
                raise TemplateFailure("invalid", "请选择要复制的模板")
            original = self._find(data.copy_of)
            latest = self.repo.latest(original)
            source = f"copy:{original.id}"
            title = data.title or f"{original.title}（副本）"
            category, description = original.category, original.description
            definition = latest.definition
            labels = (list(original.domains), list(original.countries), list(original.tags))
        elif data.content is not None:
            # 空白模板：编辑页里填好内容、点了保存才建
            content = data.content
            source, title = "blank", content.title.strip()
            category, description = content.category.strip(), content.description.strip()
            definition = definition_of(content)
            labels = (content.domains, content.countries, content.tags)
        else:
            raise TemplateFailure("invalid", "空白模板要先填好内容再保存")
        found = self._new(title, category, description, source, definition, "新建", labels=labels)
        return self._mine_out(found)

    def save(self, template_id: uuid.UUID, data: TemplateIn) -> TemplateOut:
        """保存一个新版本。用过旧版本的文档不受影响；以后上传、重新处理用新版本。"""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        latest = self.repo.latest(found)
        definition = carry_over(definition_of(data), latest.definition, data.column_refs)
        # 只改了名字、说明、标签（定义没变）：不出新版本，用过它的文档也不用重新处理
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
        """只改功能领域、国家和地区、自定义标签（不出新版本）。"""
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
        where = f"模板「{found.title}」"
        try:
            found.domains = list(clean_labels(domains, where, "功能领域", 10))
            found.countries = list(clean_countries(countries, where))
            found.tags = list(clean_labels(tags, where, "自定义标签", 20))
        except TemplateError as err:
            raise TemplateFailure("invalid", str(err)) from err
        found.category = found.domains[0] if found.domains else ""

    def archive(self, template_id: uuid.UUID, archived: bool) -> TemplateOut:
        require(self.owner, "template:manage")
        found = self._find(template_id)
        found.archived = archived
        self._audit("template:archive" if archived else "template:restore", found)
        return self._mine_out(found)

    # ---- 导出、导入、删除 ----

    def _template_named(self, name: str) -> Template:
        """我的模板（最新版）、起步模板或内置模板。"""
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
        """模板 → 可以带到别的电脑上导入的 JSON：全部内容，连原 Excel（按原表导出用）一起。"""
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
        """模板 → 标准模板 Excel（方法一）：字段、表格、规则、对象、参照表各一张表。"""
        from jacara.workbook.standard import to_excel

        data = self.export_file(name)
        return f"{data.title}.xlsx", to_excel(data.model_dump())

    def import_file(self, data: TemplateFile) -> TemplateOut:
        """导入模板文件：新建一个我的模板（第 1 版）。规则编号不认识、字段写错了，说清楚哪里错。"""
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
            "rules": list(dict.fromkeys(data.rules)),
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
            f"导入（导出时是第 {data.version} 版）",
            labels=(
                data.domains or ([data.category.strip()] if data.category.strip() else []),
                # 旧的模板文件、标准模板 Excel 没写国家：按选的规则认
                list(infer_countries(data.rules)) if data.countries is None else data.countries,
                data.tags,
            ),
        )
        return self._mine_out(found)

    # ---- 从 Excel 建模板（ADR 0031） ----

    def analyze_excel(self, filename: str, data: bytes) -> ExcelAnalysisOut:
        """上传一个 Excel：标准模板 Excel 直接读出模板；业务报表先存起来，返回每张 sheet 的猜测。"""
        require(self.owner, "template:manage")
        from jacara.workbook.analyze import WorkbookError
        from jacara.workbook.standard import StandardError, from_excel, is_standard

        if not data:
            raise TemplateFailure("invalid", "文件是空的")
        if len(data) > MAX_EXCEL_MB * 1024 * 1024:
            raise TemplateFailure("invalid", f"Excel 超过 {MAX_EXCEL_MB} MB")
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
            raise TemplateFailure("unavailable", "对象存储暂时不可用，请稍后再试") from err
        return out

    def reanalyze_excel(self, upload_id: uuid.UUID) -> ExcelAnalysisOut:
        """再看一次上传过的 Excel（刷新预览页面时用）。"""
        require(self.owner, "template:manage")
        data, filename = self._upload(upload_id)
        return self._analysis(filename, data, upload_id)

    def build_excel(self, upload_id: uuid.UUID, choice: ExcelBuildIn) -> TemplateOut:
        """按预览里确认的结果建模板：原 Excel 存一份，以后导出时填回它。"""
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
            raise TemplateFailure("invalid", f"没有叫 {choice.starter} 的起步模板")
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
            f"从 Excel 建：{filename}",
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
            auto_columns=AUTO_COLUMNS,
        )

    def _upload(self, upload_id: uuid.UUID) -> tuple[bytes, str]:
        try:
            data = self._storage().get(_upload_key(upload_id))
            name = self._storage().get(_upload_key(upload_id) + ".name").decode()
        except StorageError as err:
            raise TemplateFailure("not_found", "找不到上传的 Excel，请重新上传") from err
        return data, name

    def _store_workbook(self, data: bytes) -> str:
        key = f"templates/files/{uuid.uuid4()}.xlsx"
        try:
            self._storage().put(key, data, XLSX)
        except StorageError as err:
            raise TemplateFailure("unavailable", "对象存储暂时不可用，请稍后再试") from err
        return key

    def _storage(self) -> ObjectStorage:
        if self.storage is None:
            raise TemplateFailure("unavailable", "对象存储没有准备好")
        return self.storage

    def delete(self, template_id: uuid.UUID) -> int:
        """删除模板（归档的、用过的都能删）。返回有几份文档用过它。

        没有文档用过：模板和全部版本直接删掉。
        有文档用过（处理过或排队等着用）：从模板列表、上传、重新处理里去掉，但版本留在数据库里，
        那些文档照常显示字段中文名、检查规则、导出（它们记着用的是哪个版本）。
        """
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

    # ---- 上传时选模板 ----

    def pick_for_upload(self, choice: str | None) -> Pick:
        """上传时选的模板 → （模板版本 id，是不是键值对发现，是不是自动判断）。

        没选：用这个人上一次上传用的；第一次上传就用内置的收据模板（老办法）。
        auto：每张单据从我的模板里自动挑（ADR 0038）。
        """
        if choice == DISCOVER:
            return Pick(None, discover=True)
        if choice == AUTO:
            return Pick(None, auto=True)
        if choice in (None, ""):
            return self._last_choice()
        template_id = _uuid(choice)
        if template_id is None:
            raise TemplateFailure("invalid", '请选择我的模板，或者"抽取所有键值对"')
        found = self._find(template_id)
        if found.archived:
            raise TemplateFailure("invalid", f"模板「{found.title}」已经归档了，不能再用来上传")
        return Pick(self.repo.latest(found).id)

    def latest_version_id(self, template_id: uuid.UUID) -> uuid.UUID:
        found = self.repo.get(template_id, self.owner.team_id)
        if found is None:
            raise TemplateFailure("invalid", "这份文档用的模板已经删除了，请选一个模板再重新处理")
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

    # ---- 试跑 ----

    def trial(self, template_id: uuid.UUID, document_id: uuid.UUID) -> TrialOut:
        """用这个模板的最新版，在一份已处理的文档上试一次：不保存，不改文档。"""
        require(self.owner, "template:manage")
        found = self._find(template_id)
        template = self._template(found, self.repo.latest(found))
        document = self._document(document_id)
        pages = [e for e in document.extractions if e.ocr is not None][:MAX_TRIAL_PAGES]
        if not pages:
            raise TemplateFailure(
                "invalid", "这份文档没有存 OCR 结果（更新前处理的），请先重新处理一次再试跑"
            )
        if self.pipeline is None:
            raise TemplateFailure("unavailable", "模型服务没有准备好")
        from jacara.pipeline.pages import pages_json, stack_pages
        from jacara.pipeline.store import page_from_json
        from jacara.structure.graph import structure_from_json

        pipeline = self.pipeline()
        started = time.monotonic()
        out: list[TrialPage] = []
        for extraction in pages:
            # 几页拼成的单据（ADR 0036）：每一页还原后按处理时一样拼起来
            stacked = stack_pages(
                [
                    page_from_json(p, p.get("engine") or extraction.ocr_engine)
                    for _, p in pages_json(extraction.ocr, extraction.page)
                ]
            )
            page = stacked.page
            # 用处理时存下来的页面结构（版面区域、表格是模型认的）；没有就按规则重新分析
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

    # ---- 用样例建模板 ----

    def from_sample(self, data: SampleIn) -> tuple[TemplateOut, uuid.UUID]:
        """样例文档（抽出了键值对）→ 新模板第 1 版，样例换成按新模板的结果（新的一轮）。

        选中的键值对原样搬过去（值、出处、人工改过的都在），不重新跑模型；
        自己加的字段是空的，等人去填。单据上的原名记成别名，以后模型更容易认出来。
        """
        require(self.owner, "template:manage")
        document = self._document(data.document_id)
        current = document.extractions
        if not current or any(e.template != DISCOVER for e in current):
            raise TemplateFailure("invalid", '这份文档不是"抽取所有键值对"处理的，不能当样例')
        sources = {v.name: v for v in current[0].values}
        fields: list[dict[str, Any]] = []
        for f in data.fields:
            item = f.model_dump(exclude={"source"})
            original = sources.get(f.source) if f.source else None
            if f.source and original is None:
                raise TemplateFailure("invalid", f"样例里没有 {f.source}")
            if original is not None and original.label and original.label != f.label:
                item["aliases"] = list(dict.fromkeys([*f.aliases, original.label]))
            fields.append(item)
        definition = {"fields": fields, "tables": [], "rules": list(dict.fromkeys(data.rules))}
        found = self._new(
            data.title.strip(),
            data.category.strip(),
            data.description.strip(),
            f"sample:{document.id}",
            definition,
            "用样例建模板",
        )
        version = self.repo.latest(found)
        template = self._template(found, version)
        self._apply_sample(document, version, template, data)
        return self._mine_out(found), document.id

    def from_boxes(self, data: BoxesIn) -> TemplateOut:
        """在样例上框好的字段 → 新模板第 1 版（ADR 0047）。字段的名称、类型、别名（框旁边的标签）、
        说明（在单据哪里）由页面上框选时填好；框的位置只记进模板的来源，不进定义。
        样例不自动换成新模板的结果：页面上可以接着"用新模板重新处理"。"""
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
            "在样例上框选建模板",
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
                run_reason="用样例建模板：选中的键值对搬过来，不重新抽取",
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
                # 第一页按字段名找；其他页按单据上的原名找同一项
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

    # ---- 内部 ----

    def session_or_fail(self) -> Session:
        if self.session is None:
            raise RuntimeError("这个操作要数据库会话")
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
        """新建模板（第 1 版）。labels 是（功能领域，国家和地区，自定义标签）；
        不给就按 category 和定义里选的规则定（从 Excel 建、用样例建）。"""
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
        """先写进数据库，再重新读版本列表（refresh 不会先写，会把刚改的值读回旧的）。"""
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
            raise TemplateFailure("not_found", "找不到这个模板")
        return found

    def _document(self, document_id: uuid.UUID) -> Document:
        from jacara.modules.documents.repository import DocumentRepository

        document = DocumentRepository(self.session_or_fail()).get(document_id, self.owner.team_id)
        if document is None:
            raise TemplateFailure("not_found", "找不到这个文档")
        if document.status not in ("done", "reviewed"):
            raise TemplateFailure("conflict", "这份文档还没处理完")
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
        # 标签以 templates 表记的为准（ADR 0037）
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
        """每个模板有几份文档用、最近一次（同一次请求里只查一次）。"""
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
    """起步模板后来加的公式（ADR 0035）：同一个字段 ID、类型一样，我的这份没有公式。"""
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
