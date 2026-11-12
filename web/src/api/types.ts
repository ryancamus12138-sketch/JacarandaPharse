/** 后端返回的数据长什么样。和 backend 的 schemas.py 一一对应，字段名保持一样。 */

export interface Health {
  status: string
  version: string
}

export interface User {
  id: string
  username: string
  display_name: string
  role: string
  permissions: string[]
  is_active: boolean
  created_at: string
}

/** GET /auth/me：现在是谁、能做什么。 */
export interface Me {
  mode: 'personal' | 'team'
  role: string
  permissions: string[]
  user: User | null
  /** 右上角显示的名字：昵称 → 用户名；个人模式没设昵称是"本机用户"。 */
  shown_name?: string
  /** 最后一次换头像的时间（也当头像的版本号）；没有头像是 null。 */
  avatar_at?: string | null
}

/** 个人资料页（GET /auth/profile）。 */
export interface Profile {
  mode: 'personal' | 'team'
  /** 登录用的名字。个人模式没有。 */
  username: string | null
  /** 昵称，可以是空的。 */
  display_name: string
  shown_name: string
  role: string
  /** 所在团队的名字。个人模式是空的。 */
  team: string
  /** 能做的事（中文名）。 */
  permissions: string[]
  avatar_at: string | null
  member_since: string | null
}

/** 备注：留给同事的话，挂在整份文档（field_name 为空）或某个字段上。 */
export interface DocComment {
  id: string
  document_id: string
  /** 字段在第几页；整份文档的备注是 null。 */
  page: number | null
  /** 字段 ID；表格里的一格是 items.2.amount。 */
  field_name: string | null
  body: string
  author: string
  /** 是不是自己写的（自己写的才能删，管理员都能删）。 */
  mine: boolean
  created_at: string
  resolved_at: string | null
  resolved_by: string | null
}

/** POST /auth/login 的返回。token 只在这里出现一次。 */
export interface LoginOut {
  token: string
  expires_at: string
  user: User
}

/** 文档状态。uploaded 排队中 → processing 处理中 → done 完成 / failed 失败；审核完是 reviewed。 */
export type DocumentStatus = 'uploaded' | 'processing' | 'done' | 'failed' | 'reviewed'

/** 文档列表里的一项（后端的 DocumentOut）。 */
export interface DocumentSummary {
  id: string
  filename: string
  content_type: string | null
  size_bytes: number
  page_count: number
  status: DocumentStatus
  /** 失败原因，只有 failed 时才有。 */
  error: string | null
  created_at: string
  /** 还在等人确认的字段数。 */
  review_count: number
  /** 处理到哪一步，比如"抽取第 2 / 3 页"。只有处理中才有。 */
  stage: string | null
  /** 处理进度 0–100。只有处理中才有。 */
  progress: number | null
  processed_at: string | null
  reviewed_at: string | null
  /** 现在用的是第几轮处理的结果（重新处理一次加 1）。 */
  current_run?: number
  /** 不用模板、抽出所有键值对（"用样例建模板"的第一步）。 */
  discover?: boolean
  /** 自动判断用哪个模板（ADR 0038）。 */
  auto?: boolean
  /** 看不看原图：always、off；null 按系统设置（ADR 0038）。 */
  vision?: string | null
  /** 审核时点了"先跳过"的时间；没跳过是 null。 */
  skipped_at?: string | null
  skip_reason?: string | null
  /** 还没解决的备注几条。 */
  open_comments?: number
  /** 批量导入时所在的文件夹（ADR 0036）。 */
  folder?: string | null
  /** 一个文件里有几张单据：auto 自动判断、one 整个文件一张、pages 每页一张，或页码范围 "1-2, 3"。 */
  split?: string
  /** 内容完全一样的文件之前也传过：那几份。 */
  same_file?: { id: string; filename: string; created_at: string }[]
  /** 批量导入选了"跳过重复的文件"：这次没有新建，返回的是之前那份。 */
  duplicate_skipped?: boolean
}

/** 批量导入的一个文件夹和里面有几份文档。 */
export interface FolderCount {
  folder: string
  count: number
}

/** 文档列表的筛选条件。都不填 = 全部。 */
export interface DocumentFilters {
  status?: DocumentStatus[]
  template?: string
  /** YYYY-MM-DD，含这一天 */
  date_from?: string
  date_to?: string
  /** 文件名里包含 */
  q?: string
  /** 只看还有字段等人确认的 */
  pending?: boolean
  /** 字段内容里包含（例如发票号码、商家名） */
  value_q?: string
  /** 和 value_q 一起用：只在这个字段里找 */
  field?: string
  /** true 只看跳过的，false 不看跳过的 */
  skipped?: boolean
  /** 批量导入的文件夹（含子文件夹） */
  folder?: string
}

/** 审核页面的"上一份 / 下一份"（只算待审核的）。 */
export interface DocumentNeighbors {
  previous: string | null
  next: string | null
  /** 除了这一份，还有几份待审核。 */
  remaining: number
}

/** 工作台（GET /dashboard）。 */
export interface Dashboard {
  counts: {
    queued: number
    processing: number
    to_review: number
    failed: number
    reviewed: number
    total: number
    /** 待审核里先跳过的有几份。 */
    skipped?: number
    pending_fields: number
    processed_today: number
    reviewed_today: number
    average_page_seconds: number | null
  }
  /** 排队中和处理中的（带进度）。 */
  queue: DocumentSummary[]
  failed: DocumentSummary[]
  /** 待审核里等人确认的字段最多的几份。 */
  to_review: DocumentSummary[]
}

/** 一次导出（GET /exports）。 */
export interface ExportRecord {
  id: string
  filename: string
  format: 'xlsx' | 'zip' | 'csv' | 'csv-zip'
  size_bytes: number
  document_count: number
  documents: { id: string; filename: string }[]
  created_at: string
  user_name: string
  /** 文件还在，可以重新下载。 */
  available: boolean
}

export interface ExportPage {
  items: ExportRecord[]
  total: number
}

/** 系统信息（GET /system）。 */
export interface SystemInfo {
  version: string
  mode: 'personal' | 'team'
  model: {
    server: string
    file: string | null
    loaded: string | null
    context: number | null
    /** 视觉模型文件（mmproj，ADR 0038）。 */
    vision_file?: string | null
    /** 模型服务现在能不能看图。 */
    sees_images?: boolean
    /** 什么时候看原图：auto、always、off。 */
    vision_mode?: string
  }
  ocr: { engine: string; version: string | null }
  /** 页面结构分析用的模型（ADR 0034）。null：没开。 */
  structure?: { layout: string | null; table: string | null } | null
  /** 监视文件夹（放进去的文件自动导入）；没设是 null。 */
  watch_dir?: string | null
  templates: { name: string; title: string; version: number }[]
  data: {
    documents: number
    pages: number
    fields: number
    revisions: number
    document_bytes: number
    exports: number
    export_bytes: number
  }
  services: { name: string; ok: boolean; detail: string; hint: string }[]
}

/** 分页的列表（后端的 DocumentPage）。 */
export interface DocumentPage {
  items: DocumentSummary[]
  total: number
}

/** 一个值的出处：在 OCR 结果的第几行、那一行的原文、在页面图片上的框。 */
export interface Evidence {
  /** 第几个视觉行。从原文选取的出处，选的文字块不在任何行里时是 null。 */
  row: number | null
  row_text: string
  /** [左, 上, 右, 下]，单位是 /pages/{n}/image 那张图片上的像素。 */
  bbox: [number, number, number, number]
  /** 出处文字的 OCR 置信度（0–1）。 */
  confidence: number
  /** 值和原文有多像（0–1），1 是完全一样。 */
  similarity: number
  /** 出处那几块的原文文字。更新前处理的文档没有。 */
  text?: string
  /** 模型抄的原文写法（ADR 0040），数字、日期、日期范围才有。 */
  quote?: string
  /** 模型说用了哪几个文字块（编号，ADR 0034）。v3 之前处理的没有。 */
  blocks?: number[]
  /** 在文件的第几页（ADR 0036：一张单据可以占好几页）。更新前处理的没有，就是这张单据的第一页。 */
  page?: number
}

/**
 * matched 找到一样的；fuzzy 只找到相似的；not_found 原文里找不到；empty 模型说原文里没有；
 * rejected 模型给了值、原文里核对不上，按规则没有填（ADR 0034）。
 */
export type AlignStatus =
  | 'matched'
  | 'fuzzy'
  | 'not_found'
  | 'empty'
  | 'rejected'
  | 'computed'
  /** 看原图读的（ADR 0038）：OCR 认错或漏了，要人对着原图核对。 */
  | 'seen'

/** 一个字段（后端的 ValueOut）。 */
export interface FieldValue {
  id: string
  name: string
  type: FieldType
  /** 日期范围拆开的开始、结束（ADR 0039）：按当前值、后端用代码拆的。 */
  range?: { start: string | null; end: string | null; days: number | null; problem: string | null } | null
  /** 模型抽出的原值，永远不变。 */
  value: string | null
  /** 模型给了、但原文里核对不上、按规则没有填的值（ADR 0034）。核对原图后可以采用。 */
  rejected_value?: string | null
  /** 当前值：审核之后的值，界面上显示这个。 */
  current_value: string | null
  status: AlignStatus
  needs_review: boolean
  /** 有人修改或确认过。 */
  reviewed: boolean
  /** 还在等人确认：界面上标黄。 */
  pending_review: boolean
  /** 为什么要确认，比如"原文里找不到这个值"。 */
  note: string
  /** 抽取时找到的出处，永远不变。 */
  evidence: Evidence | null
  /** 当前出处：从原文选取过就是选的那几块，否则等于 evidence。原图上画这个。 */
  current_evidence: Evidence | null
  /** model：模型抽的；manual：审核时人工增加的行。 */
  origin: 'model' | 'manual' | 'computed'
  /** 这一格所在的行被删除了（表格才有）。 */
  deleted: boolean
  /** 证据（抽取时的出处）：原文一致、有疑点、原文里找不到、原文没有（ADR 0028）。 */
  evidence_state: EvidenceState
  /** 原文值：按出处文字得到的值，和当前值不一样时才有。"采用原文值"用。 */
  source_value: string | null
  /** 校验（按当前值）：没问题、违反警告级规则、违反错误级规则。 */
  check_state: CheckState | null
  /** 颜色：证据 × 校验 × 有没有人审核过。 */
  level: Level | null
  /** 中文名：模板里的名字；抽取所有键值对时是单据上的原名。 */
  label?: string | null
  /** 重新处理之后：上一轮人审核过、可以直接沿用的结果。 */
  suggestion?: Suggestion | null
}

/** 沿用上一轮的审核：confirm 确认这次的值；edit 改成上次人改的值。 */
export interface Suggestion {
  action: 'confirm' | 'edit'
  value: string | null
  run: number
}

export type EvidenceState = 'verified' | 'pending' | 'missing' | 'empty'
export type CheckState = 'ok' | 'warn' | 'fail'
export type Level = 'red' | 'yellow' | 'grey' | 'green'

/** 一条没通过的规则，比如"现金 − 总额 = 找零"。 */
export interface CheckIssue {
  rule: string
  title: string
  message: string
  fields: string[]
  severity: 'error' | 'warning'
}

export interface Checks {
  passed: string[]
  skipped: string[]
  issues: CheckIssue[]
}

/** 一页的抽取结果。 */
/** 自动判断用哪个模板的经过（ADR 0038）。 */
export interface Classified {
  picked: { template_id: string; version_id: string; title: string; version: number } | null
  reason: string
  /** model：模型选的；keywords：按关键字；none：没有可选的模板。 */
  by: 'model' | 'keywords' | 'none'
  candidates: { template_id: string; title: string; score: number }[]
}

export interface Extraction {
  id: string
  /** 这张单据从第几页开始。 */
  page: number
  /** 到第几页结束（ADR 0036）；只占一页是 null。 */
  last_page?: number | null
  /** 读文字时的特别情况，例如"第 2 页：PDF 文字层是乱码，这一页改用 OCR"。 */
  source_notes?: string[]
  source: string
  model: string
  prompt_version: string
  /** 内置模板的名字、我的模板的 id，或者 discover（没用模板，抽出所有键值对）。 */
  template: string
  template_version: number
  /** 模板的中文名。 */
  template_title?: string
  /** 自动判断用哪个模板的经过（ADR 0038）；上传时直接选了模板的是 null。 */
  classified?: Classified | null
  /** 给模型看了原图的原因（ADR 0038）；没看是 null。 */
  vision?: string | null
  run?: number
  run_reason?: string | null
  /** 字段 ID → 中文名。表格的列写成 items.amount，表名写成 items。 */
  labels?: Record<string, string>
  /** 抽取时的规则结果，永远不变。 */
  checks: Checks
  /** 按审核之后的当前值重新检查的结果（删掉的行不算）。页面显示这个。 */
  current_checks: Checks | null
  values: FieldValue[]
}

/** 文档详情：文档本身 + 每一页的抽取结果。 */
export interface DocumentDetail extends DocumentSummary {
  extractions: Extraction[]
  /** 处理过的每一轮（新的在前）。 */
  runs?: RunInfo[]
  /** extractions 是第几轮的。不是 current_run 时只能查看。 */
  viewing_run?: number
  /** 可以沿用上一轮审核的字段有几个。 */
  suggestions?: number
}

/** 处理过的一轮。 */
export interface RunInfo {
  run: number
  template: string
  template_title: string
  template_version: number
  reason: string | null
  created_at: string
  current: boolean
}

/** 重新处理的原因。 */
export type ReprocessReason =
  | 'template_changed'
  | 'wrong_template'
  | 'bad_result'
  | 'model_changed'
  | 'other'

export const REPROCESS_REASONS: Record<ReprocessReason, string> = {
  template_changed: '模板改了',
  wrong_template: '选错了模板',
  bad_result: '结果不好，再跑一次',
  model_changed: '换了模型或 OCR',
  other: '其他',
}

export type FieldType = 'string' | 'number' | 'date' | 'date_range'

/** 日期范围导出成（ADR 0039）：统一格式、原文、拆成两列。 */
export type RangeExport = 'normalized' | 'original' | 'columns'

/** 模板里的一个字段（或表格的一列）。 */
export interface TemplateField {
  /** 字段 ID：小写英文字母开头，只有小写字母、数字、下划线。保存后不要改。 */
  name: string
  label: string
  type: FieldType
  required: boolean
  description: string
  /** 单据上的其他写法，一起告诉模型。 */
  aliases: string[]
  /** 导出 Excel 时要不要这一列。 */
  export: boolean
  /** 敏感信息（证件号码等），默认不导出。 */
  sensitive: boolean
  /** 可选值：抽到的值不在里面就提醒。空 = 不限。 */
  options: string[]
  /** false = 人工填：不交给模型，审核时填。 */
  extract: boolean
  /** 计算字段的公式（ADR 0035），例如 check_out - check_in。空 = 不是计算字段。 */
  compute?: string
  /** 不能重复（ADR 0036）：发票号码这类，别的单据里有一样的值就提醒可能重复报销。 */
  unique?: boolean
  /** 日期范围导出成什么样（ADR 0039），默认统一格式。 */
  range_export?: RangeExport
  /** 统一格式里开始、结束之间写什么，默认 " ~ "。 */
  range_separator?: string
}

export interface TemplateTable {
  name: string
  label: string
  description: string
  required: boolean
  export: boolean
  columns: TemplateField[]
}

export interface TemplateRule {
  id: string
  title: string
  needs: string[]
  severity: 'error' | 'warning'
}

/** 规则库里的一条规则（编辑器里选）。 */
export interface RuleInfo extends TemplateRule {
  per_row: string | null
  /** 只适用于某个国家的规则：国家代码（CN、AU）；通用的是空的。 */
  country: string
}

/** 自己写的规则（ADR 0035）：条件例如 sum(items.amount) == total。 */
export interface CustomRule {
  title: string
  check: string
  severity: 'error' | 'warning'
  /** 写表名：每一行单独检查，条件里写这张表的列名。 */
  per_row: string | null
}

/** mine：我的模板；starter：起步模板库；builtin：内置（评测、命令行用）。 */
export type TemplateKind = 'mine' | 'starter' | 'builtin'

export interface Template {
  name: string
  version: number
  title: string
  description: string
  category: string
  kind: TemplateKind
  id: string | null
  archived: boolean
  source: string
  updated_at: string | null
  fields: TemplateField[]
  tables: TemplateTable[]
  rules: TemplateRule[]
  custom_rules?: CustomRule[]
  /** 功能领域（发票、交通……），可以有几个。 */
  domains?: string[]
  /** 国家和地区代码（CN、AU……）；空 = 通用。 */
  countries?: string[]
  /** 自定义标签。 */
  tags?: string[]
  /** 最近一次有文档用它的时间。 */
  last_used_at?: string | null
  /** 从起步模板复制的：起步模板后来加的公式（字段 ID → 公式），这份还没有。 */
  starter_formulas?: Record<string, string>
  versions: { version: number; note: string; created_at: string }[]
  used: number
  /** 对象：几份单据说的同一个人、同一家公司，导出时按编号合并成一行。 */
  entities?: { name: string; label: string; key: string; fields: string[] }[]
  /** 参照表：固定名单，用来对照、补编号。 */
  references?: {
    name: string
    label: string
    columns: string[]
    row_count: number
    match_field: string
    match_column: string
  }[]
  /** 按原 Excel 导出的样式。 */
  workbook?: {
    filename: string
    sheets: {
      name: string
      role: SheetRole
      header_row: number
      same_as: string
      table: string
      entity: string
      reference: string
      columns: { index: number; header: string; ref: string }[]
    }[]
  } | null
}

export type SheetRole = 'document' | 'items' | 'entity' | 'reference' | 'options' | 'ignore'
export type ColumnSource = 'extract' | 'manual' | 'doc' | 'auto' | 'lookup' | 'none'

/** Excel 的一列：表头、前几个值、猜的来源。预览里可以改。 */
export interface ExcelColumn {
  index: number
  header: string
  samples: string[]
  source: ColumnSource
  field: string
  label: string
  type: FieldType
  required: boolean
  sensitive: boolean
  description: string
  aliases: string[]
  options: string[]
  auto: string
  /** 「参照表 sheet 名::列名」 */
  lookup: string
}

export interface ExcelSheet {
  name: string
  role: SheetRole
  header_row: number
  rows: number
  hidden: boolean
  same_as: string
  /** 对象表、参照表：编号列；明细表：连到单据的那一列。 */
  key_header: string
  /** 参照表：「sheet 名::表头」，拿那一列的值来对照。 */
  match_field: string
  reason: string
  columns: ExcelColumn[]
}

/** 上传 Excel 的结果：标准模板 Excel（直接给出模板），或者业务报表（每张 sheet 的猜测）。 */
export interface ExcelAnalysis {
  kind: 'standard' | 'report'
  filename: string
  template: (TemplateIn & { version: number }) | null
  upload_id: string | null
  sheets: ExcelSheet[]
  starter: string
  starter_title: string
  auto_columns: Record<string, string>
}

/** 保存模板（每次保存是一个新版本）。 */
export interface TemplateIn {
  title: string
  category: string
  description: string
  fields: TemplateField[]
  tables: TemplateTable[]
  rules: string[]
  custom_rules: CustomRule[]
  domains: string[]
  countries: string[]
  tags: string[]
  note: string
  /** 从 Excel 建的模板：改原表某一列填什么。键"sheet 名::表头"，值是字段 ID 或 period.start 这种（ADR 0039）。 */
  column_refs?: Record<string, string>
}

/** 用样例建模板：选的一个字段。source 是样例里的键值对（kv.3）；自己加的为空。 */
export interface SampleField extends TemplateField {
  source: string | null
}

export interface TrialResult {
  template: string
  version: number
  duration_ms: number
  pages: {
    page: number
    fields: { name: string; label: string; value: string | null; status: AlignStatus; note: string }[]
    issues: { rule: string; title: string; message: string; severity: 'error' | 'warning' }[]
    passed: string[]
  }[]
}

/** 一次审核（POST /values/{id}/revisions）。edit：改成 value；confirm：当前值是对的。 */
export interface RevisionIn {
  action: 'edit' | 'confirm'
  /** 新值。只有 edit 用；null 表示"原文里确实没有这个字段"。 */
  value?: string | null
  /** 为什么这样改。修改时必须写（从原文选取时可以不写）。 */
  reason: string
  /** 从原文选取：这一页 OCR 文字块的编号（OcrLine.index）。值从它们的文字读出，出处换成它们。 */
  source_lines?: number[]
  /** 框选：原图上框出的区域 [x0, y0, x1, y1]（重新识别之后确认的值放在 value）。出处就是这个框。 */
  source_box?: Box
  /** 从原文选取、框选的是第几页（一张单据可以占好几页）。 */
  page?: number
}

/** 页面上的一个框 [左, 上, 右, 下]，和页面图片一样的像素坐标。 */
export type Box = [number, number, number, number]

/** 重新识别一块区域的结果（POST /documents/{id}/pages/{n}/ocr-region）。 */
export interface RegionOut {
  text: string
  confidence: number | null
}

/** 原图上的一个文字块（GET /documents/{id}/pages/{n}/ocr）。 */
export interface OcrLine {
  index: number
  text: string
  /** [左, 上, 右, 下]，和页面图片一样的像素坐标。 */
  box: [number, number, number, number]
  polygon: [number, number][]
  confidence: number
  row: number | null
}

/** 一页的 OCR 结果。available=false：这一页处理时还没保存 OCR 结果。 */
export interface PageOcr {
  available: boolean
  lines: OcrLine[]
}

/** 一条审核记录。 */
export interface Revision {
  id: string
  seq: number
  /** edit 修改、confirm 确认、delete 删除这一行、restore 恢复这一行、compute 按公式重算（ADR 0035）。 */
  action: 'edit' | 'confirm' | 'delete' | 'restore' | 'compute'
  new_value: string | null
  reason: string
  /** 从原文选取时的新出处。 */
  evidence: Evidence | null
  /** 谁做的。 */
  user_id: string
  created_at: string
}

/** 一个字段的完整经历：模型原值 + 每一次审核。 */
export interface ValueHistory {
  value: FieldValue
  revisions: Revision[]
}

/** 管理员可以开通给使用者的一项权限（GET /users/permissions）。 */
export interface PermissionOption {
  name: string
  label: string
}

export type Role = 'admin' | 'member'

/** 新建用户（POST /users）。 */
export interface UserCreate {
  username: string
  password: string
  display_name: string
  role: Role
  permissions: string[]
}

/** 修改用户（PATCH /users/{id}）：只发要改的字段。 */
export interface UserUpdate {
  role?: Role
  permissions?: string[]
  is_active?: boolean
  display_name?: string
}

/** 管理员生成的一次性重设码（只出现这一次）。 */
export interface ResetCode {
  username: string
  code: string
  expires_at: string
}

/** 一条审计日志。 */
export interface AuditLog {
  id: string
  created_at: string
  /** 谁做的。登录失败且用户名不存在时是 null。 */
  user_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown>
}

export interface AuditPage {
  items: AuditLog[]
  total: number
}

/** 某一页的结构（ADR 0034）：版面区域、表格的格子、标题 → 值。审核页"版面结构"画在原图上。 */
export interface PageStructure {
  /** 2026-11-14 之前处理的没有。 */
  available: boolean
  sources: Record<string, string>
  regions: { kind: string; box: Box }[]
  tables: {
    box: Box
    rows: number
    cols: number
    /** model：表格结构模型认的；rule：按文字位置划分的。 */
    by: string
    cells: { row: number; col: number; row_span: number; col_span: number; box: Box; text: string }[]
  }[]
  pairs: { key: string; key_box: Box; value: string; value_box: Box; rule: string }[]
}
