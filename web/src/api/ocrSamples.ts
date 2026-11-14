/** OCR correction samples: /api/v1/ocr-samples. */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'
import type { Dataset } from './hub'
import { t } from '../i18n'

export type SampleCategory = 'rec_error' | 'correct' | 'missed' | 'unclear'
/** pending: recovered from earlier corrections; becomes ready (exportable) after verification. */
export type SampleStatus = 'ready' | 'failed' | 'withdrawn' | 'pending'
export type SourceFormat = 'pdf' | 'image'

export interface SampleSettings {
  /** Whether OCR correction sample collection is enabled. */
  collect: boolean
  /** Whether the current user may change the switch (administrators). */
  can_change: boolean
}

export interface OcrSample {
  id: string
  created_at: string
  document_id: string
  filename: string
  page: number
  /** line: a clicked source block; box: a selected region. */
  kind: 'line' | 'box'
  /** Transcription: the text exactly as on the image; the training label. */
  transcription: string
  /** What OCR read at the time. null = the box contained no OCR text block. */
  ocr_text: string | null
  category: SampleCategory
  error_tags: string[]
  /** Template name in the reader's language. */
  template: string
  /** Template identity (filter value): the same template whatever language it is shown in. */
  template_key?: string
  /** Field ID: the same field whatever language it is shown in. */
  field: string
  /** Field name in the reader's language. */
  field_label: string
  source_format: SourceFormat
  ocr_engine: string
  width: number | null
  height: number | null
  status: SampleStatus
  /** Cause of the cropping failure. */
  error: string
  /** review (collected during review) or backfill (recovered from earlier corrections). */
  origin?: 'review' | 'backfill' | 'confirmed'
  /** Backfilled samples: the value the reviewer entered at the time (a reference during verification). */
  reviewed_value?: string | null
}

export interface SampleList {
  items: OcrSample[]
  total: number
  /** Count per category among non-withdrawn samples. */
  categories: Partial<Record<SampleCategory, number>>
  statuses: Partial<Record<SampleStatus, number>>
}

export interface SampleFilters {
  category?: SampleCategory[]
  status?: SampleStatus
  source_format?: SourceFormat
  template?: string
}

export type Split = 'train' | 'val' | 'test_locked' | 'quarantined'
export type ExportKind = 'train_val' | 'test'

export const SPLITS: Record<Split, { name: string; color: string }> = {
  train: { get name() { return t('训练集') }, color: 'blue' },
  val: { get name() { return t('验证集') }, color: 'purple' },
  test_locked: { get name() { return t('锁定测试集') }, color: 'gold' },
  quarantined: { get name() { return t('隔离') }, color: 'red' },
}

export interface SplitCount {
  groups: number
  rows: number
}

/** Lines per template, category or origin. label: display name of the key (template names in the reader's language). */
export interface Distribution {
  by: 'template' | 'category' | 'source_format'
  key: string
  label?: string
  train: number
  val: number
  test: number
}

export interface ExportPreview {
  kind: ExportKind
  included: number
  categories: Partial<Record<SampleCategory, number>>
  excluded: { reason: string; count: number }[]
  /** Groups and lines in train and val (or test). */
  splits: Partial<Record<'train' | 'val' | 'test', SplitCount>>
  split_version: number
  /** Whether the validation set is large enough to support conclusions. */
  evidence: { level: 'process' | 'thin' | 'trend' | 'none'; message: string } | null
  /** Number of groups per category in the validation set. */
  val_category_groups: Partial<Record<SampleCategory, number>>
  distributions: Distribution[]
  /** Recommended minimum number of lines for fine-tuning a PaddleOCR recognition model. */
  paddle_minimum?: number
}

export interface GroupMember {
  id: string
  document_id: string
  filename: string
  first_page: number
  source_format: SourceFormat
  samples: number
}

export interface SampleGroup {
  group_id: string
  /** Group id in export manifests (prefixed g_). */
  code: string
  split: Split
  reason: string
  members: GroupMember[]
  samples: number
  exported_as: string[]
}

export interface GroupList {
  items: SampleGroup[]
  split_version: number
  counts: Partial<Record<Split, number>>
}

export interface Unit {
  document_id: string
  filename: string
  page: number
  last_page: number
  template: string
  values: Record<string, string>
  group_id: string | null
  samples: number
}

export interface PairResult {
  group_id: string | null
  split: Split | null
  quarantined: boolean
  affected_exports: number
  message: string
}

export interface ExportRecord {
  id: string
  created_at: string
  /** det, det_test: detection sample exports. */
  kind: ExportKind | 'det' | 'det_test'
  split_version: number
  counts: Partial<Record<'train' | 'val' | 'test', SplitCount>>
  filters: { category?: string[]; source_format?: string | null; template?: string | null }
  affected: { group: string; reason: string; at: string }[]
}

/** Display name and color of each category. */
export const CATEGORIES: Record<SampleCategory, { name: string; color: string; hint: string }> = {
  rec_error: { get name() { return t('认错字') }, color: 'red', get hint() { return t('点的是一块原文，图中原文和 OCR 读的不一样') } },
  correct: { get name() { return t('认对的行') }, color: 'green', get hint() { return t('点的是一块原文，OCR 本来就读对了。训练时和错例一起用') } },
  missed: { get name() { return t('漏认') }, color: 'orange', get hint() { return t('框选的，框里原来没有 OCR 文字块') } },
  unclear: { get name() { return t('原因未确定') }, color: 'default', get hint() { return t('框选的，框里有 OCR 文字块，说不清是哪一步错的') } },
}

export const TAGS: Record<string, string> = {
  get substitution() { return t('字认错') },
  get missing() { return t('漏字') },
  get extra() { return t('多字') },
  get format() { return t('空格、大小写、全角半角') },
  get empty() { return t('整行没认出') },
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

/** A template that has samples: key is the filter value, title its name in the reader's language. */
export interface SampleTemplate {
  key: string
  title: string
}

export function sampleTemplates(): Promise<SampleTemplate[]> {
  return api<SampleTemplate[]>('/ocr-samples/templates')
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

/**
 * Collect correctly read lines: uncorrected values whose evidence is one whole block containing only the
 * value become pending samples.
 */
export function collectConfirmed(): Promise<{ created: number; skipped: { reason: string; count: number }[] }> {
  return api(`/ocr-samples/confirmed-lines`, { method: 'POST' })
}

/** Approve several pending samples at once. */
export function verifySamples(ids: string[]): Promise<{ verified: number }> {
  return api(`/ocr-samples/verify`, { method: 'POST', json: { ids } })
}

/**
 * Backfill samples from earlier corrections: corrections made by clicking source text or selecting a region
 * become pending samples.
 */
export function backfillSamples(): Promise<{ created: number; skipped: { reason: string; count: number }[] }> {
  return api(`/ocr-samples/backfill`, { method: 'POST' })
}

export function retrySample(id: string): Promise<OcrSample> {
  return api<OcrSample>(`/ocr-samples/${id}/retry`, { method: 'POST' })
}

export function previewExport(filters: SampleFilters, kind: ExportKind = 'train_val'): Promise<ExportPreview> {
  return api<ExportPreview>(`/ocr-samples/export/preview?${query(filters, { kind })}`)
}

export function downloadSamples(filters: SampleFilters, kind: ExportKind = 'train_val'): Promise<DownloadedFile> {
  return apiDownload(`/ocr-samples/export?${query(filters, { kind })}`, t('OCR样本.zip'))
}

export function listGroups(): Promise<GroupList> {
  return api<GroupList>('/ocr-samples/groups')
}

export function setGroupSplit(groupId: string, split: Split): Promise<SampleGroup> {
  return api<SampleGroup>(`/ocr-samples/groups/${groupId}/split`, { method: 'PUT', json: { split } })
}

export function detachMember(memberId: string): Promise<PairResult> {
  return api<PairResult>(`/ocr-samples/groups/members/${memberId}/detach`, { method: 'POST' })
}

export function listPairs(): Promise<{ a: Unit; b: Unit }[]> {
  return api<{ a: Unit; b: Unit }[]>('/ocr-samples/pairs')
}

export function decidePair(a: Unit, b: Unit, decision: 'same' | 'different'): Promise<PairResult> {
  return api<PairResult>('/ocr-samples/pairs', {
    method: 'POST',
    json: {
      a: { document_id: a.document_id, page: a.page },
      b: { document_id: b.document_id, page: b.page },
      decision,
    },
  })
}

export function findUnits(q: string): Promise<Unit[]> {
  return api<Unit[]>(`/ocr-samples/units?${new URLSearchParams({ q })}`)
}

export function listExports(): Promise<ExportRecord[]> {
  return api<ExportRecord[]>('/ocr-samples/exports')
}

// ---- Storing in the dataset library ----

/**
 * Same as the ZIP export (an export record is written as usual), but stored directly in the dataset library
 * for OCR model evaluation.
 */
export function saveSamplesAsDataset(
  filters: SampleFilters,
  kind: ExportKind,
  data: { name: string; description: string },
): Promise<Dataset> {
  return api<Dataset>(`/ocr-samples/export/dataset?${query(filters, { kind })}`, { method: 'POST', json: data })
}

// ---- Detection samples ----

/**
 * none: never saved (pre-annotation shown when opened); draft: saved, not confirmed; confirmed: exported;
 * excluded: page not used.
 */
export type DetStatus = 'none' | 'draft' | 'confirmed' | 'excluded'

export const DET_STATUS: Record<DetStatus, { name: string; color: string }> = {
  none: { get name() { return t('还没做') }, color: 'default' },
  draft: { get name() { return t('没确认') }, color: 'orange' },
  confirmed: { get name() { return t('确认了') }, color: 'green' },
  excluded: { get name() { return t('不用') }, color: 'default' },
}

export interface DetBox {
  /** Four corners [[x, y] × 4] in page image pixels, clockwise from top left. */
  points: [number, number][]
  text: string
  /** Illegible: exported as ### and excluded from training. */
  ignore: boolean
  /**
   * ocr: detected by OCR; sample: transcription corrected during review; missed: region selected during
   * review; manual: drawn here.
   */
  origin: 'ocr' | 'sample' | 'missed' | 'manual'
}

export interface DetPageItem {
  document_id: string
  filename: string
  page: number
  status: DetStatus
  boxes: number
  revision: number
  confirmed_at: string | null
}

export interface DetPageList {
  items: DetPageItem[]
  total: number
  /** Pages per status; sensitive is the number of pages excluded because of sensitive fields. */
  counts: Partial<Record<DetStatus | 'sensitive', number>>
}

export interface DetPage {
  document_id: string
  filename: string
  page: number
  width: number
  height: number
  status: DetStatus
  revision: number
  boxes: DetBox[]
  ocr_engine: string
  confirmed_at: string | null
}

export interface DetExportPreview {
  kind: ExportKind
  included: number
  boxes: number
  splits: Partial<Record<'train' | 'val' | 'test', SplitCount>>
  excluded: Record<string, number>
  split_version: number
}

export function listDetPages(status: DetStatus | '', offset: number, limit: number): Promise<DetPageList> {
  const q = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (status) q.set('status', status)
  return api<DetPageList>(`/ocr-samples/det-pages?${q}`)
}

export function getDetPage(documentId: string, page: number): Promise<DetPage> {
  return api<DetPage>(`/ocr-samples/det-pages/${documentId}/${page}`)
}

export function saveDetPage(
  documentId: string,
  page: number,
  data: { revision: number; boxes: DetBox[] },
  action?: 'confirm' | 'exclude' | 'reopen',
): Promise<DetPage> {
  const url = `/ocr-samples/det-pages/${documentId}/${page}${action ? `/${action}` : ''}`
  return api<DetPage>(url, { method: action ? 'POST' : 'PUT', json: data })
}

export function previewDetExport(kind: ExportKind): Promise<DetExportPreview> {
  return api<DetExportPreview>(`/ocr-samples/det-pages/export/preview?kind=${kind}`)
}

export function downloadDetPages(kind: ExportKind): Promise<DownloadedFile> {
  return apiDownload(`/ocr-samples/det-pages/export?kind=${kind}`, t('检测样本.zip'))
}

export function saveDetPagesAsDataset(kind: ExportKind, data: { name: string; description: string }): Promise<Dataset> {
  return api<Dataset>(`/ocr-samples/det-pages/export/dataset?kind=${kind}`, { method: 'POST', json: data })
}
