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
