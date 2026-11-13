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
  /** 错误归因（ADR 0053）。 */
  attribution_id: string | null
  stage: Stage | null
  stage_name: string
  stage_reason: string
  overridden: boolean
  auto_stage: Stage | null
  evidence: Evidence[]
}

export type Stage = 'ocr_misread' | 'ocr_uncovered' | 'extraction' | 'matching' | 'template' | 'mixed' | 'unknown'

/** 归因的证据：OCR 样本、发给模型的文字、审核时点的原文或框、模型给的原值、模板设置。 */
export interface Evidence {
  kind: 'ocr_sample' | 'sent_text' | 'revision' | 'value' | 'template'
  stage?: Stage
  ocr_text?: string
  transcription?: string
  lines?: string[]
  blocks?: number[]
  source?: 'sent' | 'ocr'
  drawn?: boolean
  rejected_value?: string | null
  value?: string
  day_first?: boolean
  [key: string]: unknown
}

export interface StageCount {
  stage: Stage
  name: string
  count: number
}

/** 人改判错在哪一步；stage 为 null 是撤销改判。 */
export function overrideAttribution(id: string, stage: Stage | null, note: string) {
  return api<{ id: string; stage: Stage; auto_stage: Stage; overridden: boolean; note: string }>(
    `/insights/attributions/${id}`,
    { method: 'PATCH', json: { stage, note } },
  )
}

export interface CorrectionStats {
  documents: number
  fields: number
  edited: number
  confirmed: number
  silent: number
  rate: number
  /** 平均每份审核用了几秒（只算记了用时的单据）。 */
  review_seconds: number | null
  /** 记了审核用时的有几份。 */
  timed_documents: number
  kinds: KindCount[]
  templates: TemplateStat[]
  by_field: FieldStat[]
  recent: Correction[]
  stages: StageCount[]
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
