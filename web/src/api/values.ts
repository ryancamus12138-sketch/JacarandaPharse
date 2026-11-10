/** 字段审核的接口：/api/v1/values。 */

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
