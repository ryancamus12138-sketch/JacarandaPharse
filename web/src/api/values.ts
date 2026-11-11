/** 字段审核的接口：/api/v1/values（一个字段）和 /api/v1/extractions（表格增删行）。 */

import { api } from './client'
import type { FieldValue, RevisionIn, ValueHistory } from './types'

/** 修改或确认一个字段。原值不会被覆盖，后端新增一条审核记录。 */
export function reviseValue(id: string, change: RevisionIn): Promise<FieldValue> {
  return api<FieldValue>(`/values/${id}/revisions`, { method: 'POST', json: change })
}

/** 一个字段的修改历史。 */
export function getValueHistory(id: string): Promise<ValueHistory> {
  return api<ValueHistory>(`/values/${id}/revisions`)
}

/** 表格加一行，插在第 after 行后面（行的编号，items.3.amount 里的 3；0 是最前面；不写加在最后）。 */
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

/** 删除或恢复表格的一行（原因必填）。不真的删，可以恢复。 */
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

/** 一次确认好几个字段（值是对的）。已经删掉的行会跳过。 */
export function confirmValues(
  ids: string[],
  reason = '',
): Promise<{ confirmed: number; skipped: number }> {
  return api<{ confirmed: number; skipped: number }>('/values/confirm', {
    method: 'POST',
    json: { ids, reason },
  })
}
