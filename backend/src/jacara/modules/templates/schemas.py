"""模板接口收发的 JSON（ADR 0030）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TemplateKind = Literal["mine", "starter", "builtin"]
"""mine：我的模板（数据库里，可以改）；starter：起步模板库；builtin：内置（评测、命令行用）。"""


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
    """计算字段的公式（ADR 0035），空 = 不是计算字段。"""
    unique: bool = False
    """不能重复（ADR 0036）：别的文档里有一样的值就提醒可能重复报销。"""
    range_export: str = "normalized"
    """日期范围导出成：normalized 统一格式、original 原文、columns 拆成两列（ADR 0039）。"""
    range_separator: str = " ~ "
    names: dict[str, str] = {}
    """别的语言的名字（ADR 0048）：语言代码 → 名字。label 是默认的中文名。"""


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


class EntityOut(BaseModel):
    """对象：几份单据说的同一个人、同一家公司，导出时按编号合并成一行。"""

    name: str
    label: str
    key: str
    fields: list[str]


class ReferenceOut(BaseModel):
    """参照表（只给概要，数据可能有几千行）。"""

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
    """按原 Excel 导出的样式：原文件名和每张 sheet 的角色、每一列填什么。"""

    filename: str
    sheets: list[WorkbookSheetOut]


class TemplateVersionOut(BaseModel):
    version: int
    note: str
    created_at: datetime


class CustomRuleIO(BaseModel):
    """自己写的规则（ADR 0035）：条件例如 sum(items.amount) == total。"""

    title: str = Field(default="", max_length=100)
    check: str = Field(min_length=1, max_length=500)
    severity: Literal["error", "warning"] = "error"
    per_row: str | None = None
    """写表名：每一行单独检查，条件里写这张表的列名。"""


class FormulaCheckIn(BaseModel):
    """编辑器里边写边检查公式或规则条件：用编辑区现在的字段（还没保存也行）。"""

    text: str = Field(max_length=500)
    kind: Literal["formula", "condition"] = "formula"
    fields: list[TemplateFieldIn] = Field(default=[], max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    row: str | None = None
    """表格的列的公式、"每一行"的规则：写表名，名字按这张表的列找。"""


class FormulaCheckOut(BaseModel):
    ok: bool
    readable: str = ""
    """换成中文名的写法：check_out - check_in → 离店日期 − 入住日期。"""
    message: str = ""
    """写错了哪里。"""


class TemplateOut(BaseModel):
    name: str
    """内置和起步模板是文件名（receipt、cn_einvoice）；我的模板是 id。"""
    version: int
    title: str
    titles: dict[str, str] = {}
    """模板名的别的语言写法（ADR 0048）。"""
    languages: list[str] = ["zh"]
    """能切换的语言，第一个是默认的中文。"""
    basis: list[str] = []
    """起步模板的依据：法规条款、使用场景、查证来源。我的模板是空的。"""
    description: str
    fields: list[TemplateFieldOut]
    rules: list[TemplateRuleOut]
    custom_rules: list[CustomRuleIO] = []
    domains: list[str] = []
    """功能领域（ADR 0037）：发票、交通、收据……可以有几个。"""
    countries: list[str] = []
    """国家和地区代码（CN、AU……）；空 = 通用。"""
    tags: list[str] = []
    """自定义标签。起步模板没有。"""
    tables: list[TemplateTableOut] = []
    """表格（例如商品明细）。每一格存成 items.2.amount 这样名字的字段。"""
    kind: TemplateKind = "builtin"
    category: str = ""
    id: uuid.UUID | None = None
    """我的模板才有。"""
    archived: bool = False
    source: str = ""
    updated_at: datetime | None = None
    versions: list[TemplateVersionOut] = []
    """我的模板的历史版本（只在"一个模板"里有）。"""
    starter_formulas: dict[str, str] = {}
    """从起步模板复制的：起步模板后来给哪些字段加了公式（字段 ID → 公式），我的这份还没有。
    编辑器里提示"套用"（ADR 0035）。表格的列写成 items.amount。"""
    used: int = 0
    """有几份文档用过这个模板（处理过或排队等着用）。我的模板才有。"""
    last_used_at: datetime | None = None
    """最近一次有文档用它是什么时候（模板页"最近用过"排序）。"""
    entities: list[EntityOut] = []
    references: list[ReferenceOut] = []
    workbook: WorkbookOut | None = None


class RuleInfo(BaseModel):
    """模板编辑器里可以选的一条规则。needs 是它要用到的字段 ID。"""

    id: str
    title: str
    needs: list[str]
    severity: str
    per_row: str | None = None
    requires: list[str] = []
    """模板里要有这些字段（值可以空）这条规则才用得上（ADR 0049）。"""
    country: str = ""
    """只适用于某个国家的规则：国家代码（CN、AU）；通用的是空的。"""


class TemplateFieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    """字段 ID：小写英文字母开头，只能有小写字母、数字、下划线。保存后不要改。"""
    label: str = Field(min_length=1, max_length=100)
    type: Literal["string", "number", "date", "date_range"] = "string"
    required: bool = False
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default=[], max_length=20)
    export: bool = True
    sensitive: bool = False
    options: list[str] = Field(default=[], max_length=500)
    """可选值：抽到的值不在里面就提醒。空 = 不限。"""
    extract: bool = True
    """False = 人工填：不交给模型，审核时填。"""
    compute: str = Field(default="", max_length=500)
    """计算字段的公式（ADR 0035），例如 check_out - check_in。
    有公式就不交给模型，由别的字段算出来。"""
    unique: bool = False
    """不能重复（ADR 0036）：发票号码这类，别的文档里有一样的值就提醒。"""
    range_export: Literal["normalized", "original", "columns"] = "normalized"
    """日期范围（ADR 0039）导出成：统一格式、原文、拆成开始和结束两列。别的类型用不到。"""
    range_separator: str = Field(default=" ~ ", min_length=1, max_length=10)
    names: dict[str, str] = Field(default={}, max_length=30)
    """别的语言的名字（ADR 0048）：{"en": "Invoice number", "ja": "請求書番号"}。"""


class TemplateTableIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    required: bool = False
    export: bool = True
    names: dict[str, str] = Field(default={}, max_length=30)
    columns: list[TemplateFieldIn] = Field(min_length=1, max_length=30)


class TemplateIn(BaseModel):
    """保存模板（每次保存是一个新版本）。"""

    title: str = Field(min_length=1, max_length=100)
    titles: dict[str, str] = Field(default={}, max_length=30)
    """模板名的别的语言写法（ADR 0048）。"""
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    rules: list[str] = []
    custom_rules: list[CustomRuleIO] = Field(default=[], max_length=30)
    domains: list[str] = Field(default=[], max_length=10)
    """功能领域，可以有几个。"""
    countries: list[str] = Field(default=[], max_length=300)
    """国家和地区代码；空 = 通用。"""
    tags: list[str] = Field(default=[], max_length=20)
    """自定义标签。"""
    note: str = Field(default="", max_length=500)
    """这一版改了什么。"""
    column_refs: dict[str, str] = Field(default={}, max_length=500)
    """从 Excel 建的模板：改原表某一列填什么（ADR 0039）。键是"sheet 名::表头"，值是字段 ID、
    period.start / period.end / period.days（日期范围的开始、结束、天数），或者空（留空）。
    不写的列不变。"""


class LabelsIn(BaseModel):
    """只改标签（不出新版本）。不写的那组不变。"""

    domains: list[str] | None = Field(default=None, max_length=10)
    countries: list[str] | None = Field(default=None, max_length=300)
    tags: list[str] | None = Field(default=None, max_length=20)


class TemplateCreate(BaseModel):
    """新建模板：空白、从起步模板复制、或者复制我的另一个模板。"""

    source: Literal["blank", "starter", "copy"]
    starter: str | None = None
    copy_of: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=100)
    content: TemplateIn | None = None
    """空白模板：编辑页里填好、点了保存才建，内容在这里（第 1 版）。"""


class ArchiveIn(BaseModel):
    archived: bool


class SampleFieldIn(TemplateFieldIn):
    """从样例里选的一个字段。source 是样例里键值对的字段名（kv.3）；自己加的字段为空。"""

    source: str | None = None


class SampleIn(BaseModel):
    """用样例建模板：样例文档（抽出了键值对的那份）+ 选了哪些、叫什么。"""

    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[SampleFieldIn] = Field(min_length=1, max_length=80)
    rules: list[str] = []


class BoxFieldIn(TemplateFieldIn):
    """框选建模板（ADR 0047）的一个字段：在样例第几页、框在哪（只用来记录，不进模板定义）。"""

    page: int = Field(default=1, ge=1)
    box: list[float] = Field(default=[], max_length=4)


class BoxesIn(BaseModel):
    """框选建模板：样例文档 + 框好的字段。样例可以是任何处理过的文档。"""

    document_id: uuid.UUID
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    fields: list[BoxFieldIn] = Field(min_length=1, max_length=80)


class SampleOut(BaseModel):
    template: TemplateOut
    document_id: uuid.UUID
    """样例文档已经换成按新模板的结果（新的一轮），可以直接审核、导出。"""


class TrialIn(BaseModel):
    document_id: uuid.UUID
    """拿哪份文档试：用它存下的 OCR 结果，不重新识别文字。"""


class TrialField(BaseModel):
    name: str
    label: str
    value: str | None
    status: str
    """matched（有出处）、fuzzy（相似）、not_found（找不到出处）、empty（原文没有）。"""
    note: str = ""


class TrialIssue(BaseModel):
    rule: str
    title: str
    message: str
    severity: str


class TrialPage(BaseModel):
    page: int
    fields: list[TrialField]
    issues: list[TrialIssue]
    passed: list[str]


class TrialOut(BaseModel):
    """试跑结果：不保存，只给人看这一版模板在这份文档上抽出什么。"""

    template: str
    version: int
    pages: list[TrialPage]
    duration_ms: int


TEMPLATE_FILE_FORMAT = "jacarandaparse-template"


class TemplateFile(BaseModel):
    """导出、导入的模板文件（JSON）。定义的格式和 .toml、数据库里的一样。"""

    format: Literal["jacarandaparse-template"] = TEMPLATE_FILE_FORMAT
    format_version: int = 1
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    version: int = 1
    """导出时是第几版（导入后从第 1 版重新算）。"""
    fields: list[TemplateFieldIn] = Field(min_length=1, max_length=80)
    tables: list[TemplateTableIn] = Field(default=[], max_length=5)
    rules: list[str] = []
    custom_rules: list[CustomRuleIO] = []
    domains: list[str] = []
    countries: list[str] | None = None
    """没有（旧的模板文件、标准 Excel）：按选的规则推断。"""
    tags: list[str] = []
    entities: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    """参照表的全部数据（columns、rows）。"""
    workbook: dict[str, Any] | None = None
    """按原 Excel 导出的样式；原 Excel 文件放在 workbook_file（base64）。"""
    workbook_file: str | None = None


# ---------- 从 Excel 建模板（ADR 0031） ----------

ColumnSource = Literal["extract", "manual", "doc", "auto", "lookup", "none"]
SheetRoleName = Literal["document", "items", "entity", "reference", "options", "ignore"]


class ExcelColumn(BaseModel):
    """一列：表头、前几个值（看一眼）、猜的类型和来源。预览里人可以改。"""

    index: int
    header: str
    samples: list[str] = []
    source: ColumnSource = "extract"
    """extract 从单据抽 / manual 人工填 / doc 单据上的字段（明细表里每行重复）/
    auto 系统自动 / lookup 从参照表对照 / none 不填。"""
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
    """「参照表 sheet 名::列名」。"""


class ExcelSheet(BaseModel):
    name: str
    role: SheetRoleName
    header_row: int
    rows: int = 0
    hidden: bool = False
    same_as: str = ""
    key_header: str = ""
    """对象表、参照表：编号列；明细表：连到单据的那一列。"""
    match_field: str = ""
    """参照表：「sheet 名::表头」，拿那一列的值来对照。"""
    reason: str = ""
    """为什么这样猜（给人看）。"""
    columns: list[ExcelColumn] = []


class ExcelAnalysisOut(BaseModel):
    """上传的 Excel 是什么：标准模板 Excel（直接给出模板），
    还是业务报表（给出每张 sheet 的猜测）。"""

    kind: Literal["standard", "report"]
    filename: str
    template: TemplateFile | None = None
    upload_id: uuid.UUID | None = None
    sheets: list[ExcelSheet] = []
    starter: str = ""
    starter_title: str = ""
    auto_columns: dict[str, str] = {}
    """系统自动填的列：auto 的值 → 中文名。"""


class ExcelBuildIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    category: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=2000)
    starter: str = ""
    """对上的起步模板：它的规则里字段都有的，一起带过来。"""
    sheets: list[ExcelSheet] = Field(min_length=1, max_length=50)
