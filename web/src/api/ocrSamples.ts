/** OCR 纠错样本：/api/v1/ocr-samples（ADR 0050）。 */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'

export type SampleCategory = 'rec_error' | 'correct' | 'missed' | 'unclear'
export type SampleStatus = 'ready' | 'failed' | 'withdrawn'
export type SourceFormat = 'pdf' | 'image'

export interface SampleSettings {
  /** 自动收集纠错样本开着没有。 */
  collect: boolean
  /** 现在这个人能不能改开关（管理员）。 */
  can_change: boolean
}

export interface OcrSample {
  id: string
  created_at: string
  document_id: string
  filename: string
  page: number
  /** line：点的一块原文；box：框选。 */
  kind: 'line' | 'box'
  /** 图中原文：图上原样的文字，训练标签。 */
  transcription: string
  /** OCR 当时读成什么。null = 框里原来没有 OCR 文字块。 */
  ocr_text: string | null
  category: SampleCategory
  error_tags: string[]
  template: string
  field: string
  field_label: string
  source_format: SourceFormat
  ocr_engine: string
  width: number | null
  height: number | null
  status: SampleStatus
  /** 裁图失败的原因。 */
  error: string
}

export interface SampleList {
  items: OcrSample[]
  total: number
  /** 没撤回的样本里每种分类各有几条。 */
  categories: Partial<Record<SampleCategory, number>>
  statuses: Partial<Record<SampleStatus, number>>
}

export interface SampleFilters {
  category?: SampleCategory[]
  status?: SampleStatus
  source_format?: SourceFormat
  template?: string
}

export interface ExportPreview {
  included: number
  categories: Partial<Record<SampleCategory, number>>
  excluded: { reason: string; count: number }[]
}

/** 分类的中文名和颜色。 */
export const CATEGORIES: Record<SampleCategory, { name: string; color: string; hint: string }> = {
  rec_error: { name: '认错字', color: 'red', hint: '点的是一块原文，图中原文和 OCR 读的不一样' },
  correct: { name: '认对的行', color: 'green', hint: '点的是一块原文，OCR 本来就读对了。训练时和错例一起用' },
  missed: { name: '漏认', color: 'orange', hint: '框选的，框里原来没有 OCR 文字块' },
  unclear: { name: '原因未确定', color: 'default', hint: '框选的，框里有 OCR 文字块，说不清是哪一步错的' },
}

export const TAGS: Record<string, string> = {
  substitution: '字认错',
  missing: '漏字',
  extra: '多字',
  format: '空格、大小写、全角半角',
  empty: '整行没认出',
}

function query(filters: SampleFilters, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams(extra)
  for (const c of filters.category ?? []) q.append('category', c)
  if (filters.status) q.set('status', filters.status)
  if (filters.source_format) q.set('source_format', filters.source_format)
  if (filters.template) q.set('template', filters.template)
  return q.toString()
}

export function getSampleSettings(): Promise<SampleSettings> {
  return api<SampleSettings>('/ocr-samples/settings')
}

export function setSampleSettings(collect: boolean): Promise<SampleSettings> {
  return api<SampleSettings>('/ocr-samples/settings', { method: 'PUT', json: { collect } })
}

export function listSamples(filters: SampleFilters, limit: number, offset: number): Promise<SampleList> {
  return api<SampleList>(`/ocr-samples?${query(filters, { limit: String(limit), offset: String(offset) })}`)
}

export function sampleTemplates(): Promise<string[]> {
  return api<string[]>('/ocr-samples/templates')
}

export function getSampleImage(id: string): Promise<Blob> {
  return apiBlob(`/ocr-samples/${id}/image`)
}

export function updateSample(
  id: string,
  change: { transcription?: string; category?: SampleCategory; status?: 'ready' | 'withdrawn' },
): Promise<OcrSample> {
  return api<OcrSample>(`/ocr-samples/${id}`, { method: 'PATCH', json: change })
}

export function retrySample(id: string): Promise<OcrSample> {
  return api<OcrSample>(`/ocr-samples/${id}/retry`, { method: 'POST' })
}

export function previewExport(filters: SampleFilters): Promise<ExportPreview> {
  return api<ExportPreview>(`/ocr-samples/export/preview?${query(filters)}`)
}

export function downloadSamples(filters: SampleFilters): Promise<DownloadedFile> {
  return apiDownload(`/ocr-samples/export?${query(filters)}`, 'OCR样本.zip')
}
