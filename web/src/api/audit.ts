/** 审计日志的接口：/api/v1/audit-logs（只有管理员能用）。 */

import { api } from './client'
import type { AuditPage } from './types'

export interface AuditFilters {
  /** 按开头筛选，比如 "document:" 只看文档相关的。 */
  action?: string
  /** 只看这个人做的。 */
  user_id?: string
  /** 日期范围（含两头，YYYY-MM-DD）。 */
  date_from?: string
  date_to?: string
}

function query(filters: AuditFilters, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams(extra)
  for (const [key, value] of Object.entries(filters)) {
    if (value) q.set(key, value)
  }
  return q.toString()
}

export function listAuditLogs(limit: number, offset: number, filters: AuditFilters = {}): Promise<AuditPage> {
  return api<AuditPage>(`/audit-logs?${query(filters, { limit: String(limit), offset: String(offset) })}`)
}

/** 按同样的条件取出全部（最多 5 万条），导出本身也会记一条审计日志。 */
export function exportAuditLogs(filters: AuditFilters): Promise<AuditPage> {
  return api<AuditPage>(`/audit-logs/export?${query(filters)}`)
}
