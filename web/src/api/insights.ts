/** 统计和数据导出：/api/v1/insights（ADR 0044）。 */

import { api, apiDownload, type DownloadedFile } from './client'

export interface KindCount {
  kind: string
  /** 中文名：小数点、日月颠倒…… */
  name: string
  count: number
}

export interface FieldStat {
  template: string
  template_title: string
  field: string
  label: string
  total: number
  edited: number
  /** 改了，但抽取时没标黄（错了却没提醒）。 */
  silent: number
  rate: number
  kinds: KindCount[]
}

export interface TemplateStat {
  template: string
  template_title: string
  documents: number
  fields: number
  edited: number
  silent: number
  rate: number
}

export interface Correction {
  document_id: string
  filename: string
  template_title: string
  label: string
  before: string | null
  after: string | null
  kind: string
  kind_name: string
  flagged: boolean
  reason: string
  at: string
}

export interface CorrectionStats {
  documents: number
  fields: number
  edited: number
  confirmed: number
  silent: number
  rate: number
  kinds: KindCount[]
  templates: TemplateStat[]
  by_field: FieldStat[]
  recent: Correction[]
}

export function getCorrections(template?: string, days?: number): Promise<CorrectionStats> {
  const query = new URLSearchParams()
  if (template) query.set('template', template)
  if (days) query.set('days', String(days))
  return api<CorrectionStats>(`/insights/corrections?${query}`)
}

/** 审核过的单据打包成 zip：原文件、训练数据、成对测评清单。 */
export function downloadDataset(opts: { template?: string; sensitive: boolean; files: boolean }): Promise<DownloadedFile> {
  const query = new URLSearchParams({ sensitive: String(opts.sensitive), files: String(opts.files) })
  if (opts.template) query.set('template', opts.template)
  return apiDownload(`/insights/dataset?${query}`, '审核数据.zip')
}
