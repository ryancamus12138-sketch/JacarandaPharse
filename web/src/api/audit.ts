/** Audit log API: /api/v1/audit-logs (administrators only). */

import { api } from './client'
import type { AuditPage } from './types'

export interface AuditFilters {
  /** Prefix filter, for example "document:" for document events only. */
  action?: string
  /** Only events by this actor. */
  user_id?: string
  /** Date range (inclusive, YYYY-MM-DD). */
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

/** All entries matching the same filters (at most 50,000); the export itself is also logged. */
export function exportAuditLogs(filters: AuditFilters): Promise<AuditPage> {
  return api<AuditPage>(`/audit-logs/export?${query(filters)}`)
}
