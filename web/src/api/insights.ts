/** Statistics and data export: /api/v1/insights. */

import { api, apiDownload, type DownloadedFile } from './client'
import { t } from '../i18n'

export interface KindCount {
  kind: string
  /** Display name: decimal point, day/month swapped, … */
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
  /** Corrected but not flagged at extraction (wrong without a warning). */
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
  /** Error attribution. */
  attribution_id: string | null
  stage: Stage | null
  stage_name: string
  stage_reason: string
  overridden: boolean
  auto_stage: Stage | null
  evidence: Evidence[]
}

export type Stage = 'ocr_misread' | 'ocr_uncovered' | 'extraction' | 'matching' | 'template' | 'mixed' | 'unknown'

/**
 * Attribution evidence: OCR samples, text sent to the model, source text or regions selected during review,
 * the model's original value, template settings.
 */
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

/** Override the stage at fault; stage null reverts the override. */
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
  /** Average review seconds per document (only documents with recorded review time). */
  review_seconds: number | null
  /** Number of documents with recorded review time. */
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

/** Package reviewed documents into a zip: original files, training data, paired evaluation manifest. */
export function downloadDataset(opts: { template?: string; sensitive: boolean; files: boolean }): Promise<DownloadedFile> {
  const query = new URLSearchParams({ sensitive: String(opts.sensitive), files: String(opts.files) })
  if (opts.template) query.set('template', opts.template)
  return apiDownload(`/insights/dataset?${query}`, t('审核数据.zip'))
}
