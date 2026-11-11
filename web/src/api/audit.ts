/** 审计日志的接口：/api/v1/audit-logs（只有管理员能用）。 */

import { api } from './client'
import type { AuditPage } from './types'

/** action：按开头筛选，比如 "document:" 只看文档相关的。 */
export function listAuditLogs(limit: number, offset: number, action?: string): Promise<AuditPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (action) {
    query.set('action', action)
  }
  return api<AuditPage>(`/audit-logs?${query}`)
}
