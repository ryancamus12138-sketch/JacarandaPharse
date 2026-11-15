/**
 * Field review API: /api/v1/values (single fields) and /api/v1/extractions (adding and removing table rows).
 */

import { api } from './client'
import type { FieldValue, RevisionIn, ValueHistory } from './types'

/** Edit or confirm a field. The original value is never overwritten; the backend adds a revision. */
export function reviseValue(id: string, change: RevisionIn): Promise<FieldValue> {
  return api<FieldValue>(`/values/${id}/revisions`, { method: 'POST', json: change })
}

/** History of a field. */
export function getValueHistory(id: string): Promise<ValueHistory> {
  return api<ValueHistory>(`/values/${id}/revisions`)
}

/**
 * Add a table row after row after (the row number, e.g. 3 in items.3.amount; 0 inserts at the top; omitted
 * appends at the end).
 */
export function addRow(
  extractionId: string,
  table: string,
  after: number | null,
): Promise<FieldValue[]> {
  return api<FieldValue[]>(`/extractions/${extractionId}/tables/${table}/rows`, {
    method: 'POST',
    json: { after },
  })
}

/** Delete or restore a table row (reason required). Nothing is deleted, so the row can be restored. */
export function changeRow(
  extractionId: string,
  table: string,
  index: number,
  action: 'delete' | 'restore',
  reason: string,
): Promise<FieldValue[]> {
  return api<FieldValue[]>(`/extractions/${extractionId}/tables/${table}/rows/${index}/${action}`, {
    method: 'POST',
    json: { reason },
  })
}

/** Confirm several fields at once (their values are correct). Deleted rows are skipped. */
export function confirmValues(
  ids: string[],
  reason = '',
): Promise<{ confirmed: number; skipped: number }> {
  return api<{ confirmed: number; skipped: number }>('/values/confirm', {
    method: 'POST',
    json: { ids, reason },
  })
}
