/** OCR 纠错样本：/api/v1/ocr-samples（ADR 0050）。 */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'
import type { Dataset } from './hub'

export type SampleCategory = 'rec_error' | 'correct' | 'missed' | 'unclear'
/** pending：从以前的修改补收的（ADR 0053），人复核了才变成 ready、才能导出。 */
export type SampleStatus = 'ready' | 'failed' | 'withdrawn' | 'pending'
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
  /** review（审核时收集的）或 backfill（从以前的修改补收的）。 */
  origin?: 'review' | 'backfill' | 'confirmed'
  /** 补收的：当时人把字段改成了什么（复核时参考）。 */
  reviewed_value?: string | null
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

export type Split = 'train' | 'val' | 'test_locked' | 'quarantined'
export type ExportKind = 'train_val' | 'test'

export const SPLITS: Record<Split, { name: string; color: string }> = {
  train: { name: '训练集', color: 'blue' },
  val: { name: '验证集', color: 'purple' },
  test_locked: { name: '锁定测试集', color: 'gold' },
  quarantined: { name: '隔离', color: 'red' },
}

export interface SplitCount {
  groups: number
  rows: number
}

export interface ExportPreview {
  kind: ExportKind
  included: number
  categories: Partial<Record<SampleCategory, number>>
  excluded: { reason: string; count: number }[]
  /** train、val（或 test）各几组、几行。 */
  splits: Partial<Record<'train' | 'val' | 'test', SplitCount>>
  split_version: number
  /** 验证集够不够说明问题。 */
  evidence: { level: 'process' | 'thin' | 'trend' | 'none'; message: string } | null
  /** 验证集里每种分类来自几组。 */
  val_category_groups: Partial<Record<SampleCategory, number>>
  distributions: { by: 'template' | 'category' | 'source_format'; key: string; train: number; val: number; test: number }[]
  /** PaddleOCR 微调识别模型建议至少多少行（ADR 0055）。 */
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
  /** 导出清单里的组号（g_ 开头）。 */
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
  /** det、det_test：检测样本的导出（ADR 0061）。 */
  kind: ExportKind | 'det' | 'det_test'
  split_version: number
  counts: Partial<Record<'train' | 'val' | 'test', SplitCount>>
  filters: { category?: string[]; source_format?: string | null; template?: string | null }
  affected: { group: string; reason: string; at: string }[]
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

/** 收认对的行（ADR 0055）：人没改过、出处只有这个值的一整块，进"待复核"。 */
export function collectConfirmed(): Promise<{ created: number; skipped: { reason: string; count: number }[] }> {
  return api(`/ocr-samples/confirmed-lines`, { method: 'POST' })
}

/** 一次复核通过几条待复核的。 */
export function verifySamples(ids: string[]): Promise<{ verified: number }> {
  return api(`/ocr-samples/verify`, { method: 'POST', json: { ids } })
}

/** 从以前的修改补收样本（ADR 0053）：点了原文或框了一块的，进"待复核"。 */
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
  return apiDownload(`/ocr-samples/export?${query(filters, { kind })}`, 'OCR样本.zip')
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

// ---- 存进数据集库（ADR 0059） ----

/** 和导出 ZIP 一样（照常记导出记录），直接存进数据集库，给 OCR 模型评测用。 */
export function saveSamplesAsDataset(
  filters: SampleFilters,
  kind: ExportKind,
  data: { name: string; description: string },
): Promise<Dataset> {
  return api<Dataset>(`/ocr-samples/export/dataset?${query(filters, { kind })}`, { method: 'POST', json: data })
}

// ---- 检测样本（ADR 0061） ----

/** none：还没存过（打开时给预标注）；draft：存过还没确认；confirmed：确认了，导出；excluded：不用这一页。 */
export type DetStatus = 'none' | 'draft' | 'confirmed' | 'excluded'

export const DET_STATUS: Record<DetStatus, { name: string; color: string }> = {
  none: { name: '还没做', color: 'default' },
  draft: { name: '没确认', color: 'orange' },
  confirmed: { name: '确认了', color: 'green' },
  excluded: { name: '不用', color: 'default' },
}

export interface DetBox {
  /** 四个角 [[x, y] × 4]，页面图上的像素，从左上开始顺时针。 */
  points: [number, number][]
  text: string
  /** 看不清：导出成 ###，不参与训练。 */
  ignore: boolean
  /** ocr：OCR 认的；sample：审核时改对了原文的；missed：审核时框选的漏认；manual：在这里画的。 */
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
  /** 每种状态几页；sensitive 是有敏感字段、不能做的页数。 */
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
  return apiDownload(`/ocr-samples/det-pages/export?kind=${kind}`, '检测样本.zip')
}

export function saveDetPagesAsDataset(kind: ExportKind, data: { name: string; description: string }): Promise<Dataset> {
  return api<Dataset>(`/ocr-samples/det-pages/export/dataset?kind=${kind}`, { method: 'POST', json: data })
}
