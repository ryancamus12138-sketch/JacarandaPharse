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
}

/** matched 找到一样的；fuzzy 只找到相似的；not_found 原文里找不到；empty 模型说原文里没有。 */
export type AlignStatus = 'matched' | 'fuzzy' | 'not_found' | 'empty'

/** 一个字段（后端的 ValueOut）。 */
export interface FieldValue {
  id: string
  name: string
  type: 'string' | 'number' | 'date'
  /** 模型抽出的原值，永远不变。 */
  value: string | null
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
  origin: 'model' | 'manual'
  /** 这一格所在的行被删除了（表格才有）。 */
  deleted: boolean
}

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
export interface Extraction {
  id: string
  page: number
  source: string
  model: string
  prompt_version: string
  template: string
  template_version: number
  /** 抽取时的规则结果，永远不变。 */
  checks: Checks
  /** 按审核之后的当前值重新检查的结果（删掉的行不算）。页面显示这个。 */
  current_checks: Checks | null
  values: FieldValue[]
}

/** 文档详情：文档本身 + 每一页的抽取结果。 */
export interface DocumentDetail extends DocumentSummary {
  extractions: Extraction[]
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
  /** edit 修改、confirm 确认、delete 删除这一行、restore 恢复这一行。 */
  action: 'edit' | 'confirm' | 'delete' | 'restore'
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
