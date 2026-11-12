/** 模板（只读）：/api/v1/templates。 */

import { api } from './client'

/** 一个模板的概要。完整的字段、规则以后做模板页时再加。 */
export interface TemplateSummary {
  name: string
  title: string
  version: number
}

export function listTemplates(): Promise<TemplateSummary[]> {
  return api<TemplateSummary[]>('/templates')
}
