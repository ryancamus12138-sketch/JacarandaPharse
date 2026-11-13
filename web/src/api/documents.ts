/** 文档相关的接口：/api/v1/documents。 */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'
import type {
  Box,
  Dashboard,
  DocComment,
  DocumentDetail,
  DocumentFilters,
  DocumentNeighbors,
  DocumentPage,
  DocumentSummary,
  FolderCount,
  PageOcr,
  PageStructure,
  RegionOut,
  ReprocessReason,
  SentText,
} from './types'

export function listDocuments(
  limit: number,
  offset: number,
  filters: DocumentFilters = {},
): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  for (const status of filters.status ?? []) {
    query.append('status', status)
  }
  if (filters.template) query.set('template', filters.template)
  if (filters.date_from) query.set('date_from', filters.date_from)
  if (filters.date_to) query.set('date_to', filters.date_to)
  if (filters.q?.trim()) query.set('q', filters.q.trim())
  if (filters.pending) query.set('pending', 'true')
  if (filters.value_q?.trim()) query.set('value_q', filters.value_q.trim())
  if (filters.field) query.set('field', filters.field)
  if (filters.skipped !== undefined) query.set('skipped', String(filters.skipped))
  if (filters.folder) query.set('folder', filters.folder)
  return api<DocumentPage>(`/documents?${query}`)
}

/** 上一份、下一份待审核的文档（按列表顺序）。 */
export function getNeighbors(id: string): Promise<DocumentNeighbors> {
  return api<DocumentNeighbors>(`/documents/${id}/neighbors`)
}

/** 工作台：统计、处理队列、需要先处理的。 */
export function getDashboard(): Promise<Dashboard> {
  return api<Dashboard>('/dashboard')
}

/** 上传一个文件。文件用 FormData 发（和 /docs 里选文件上传一样），字段名必须叫 file。 */
export function uploadDocument(
  file: File,
  template?: string,
  options: { folder?: string; split?: string; skipDuplicate?: boolean } = {},
): Promise<DocumentSummary> {
  const form = new FormData()
  form.append('file', file)
  if (template) form.append('template', template) // 我的模板的 id，或者 discover
  if (options.folder) form.append('folder', options.folder)
  if (options.split) form.append('split', options.split)
  if (options.skipDuplicate) form.append('skip_duplicate', 'true')
  return api<DocumentSummary>('/documents', { method: 'POST', body: form })
}

/** 批量导入过的文件夹（ADR 0036）。 */
export function listFolders(): Promise<FolderCount[]> {
  return api<FolderCount[]>('/documents/folders')
}

/** 重新处理（失败后重试）。 */
export function processDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/process`, { method: 'POST' })
}

/** 文档详情：每一页的字段、出处和规则校验结果。 */
export function getDocument(id: string, run?: number): Promise<DocumentDetail> {
  return api<DocumentDetail>(run ? `/documents/${id}?run=${run}` : `/documents/${id}`)
}

/** 换模板重新处理（新的一轮）。template 不写：用现在这个模板的最新版。 */
export function reprocessDocument(
  id: string,
  body: {
    template?: string
    reason: ReprocessReason
    note: string
    split?: string
    /** 看不看原图（ADR 0038）：always、off、default（按系统设置）。不写：和上次一样。 */
    vision?: 'always' | 'off' | 'default'
    /** PDF 怎么读（ADR 0042）：image 画成图识别、text 只读文字层、default 按系统设置。 */
    pdf_read?: 'image' | 'text' | 'default'
  },
): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/reprocess`, { method: 'POST', json: body })
}

/** 采纳上次的确认：重新处理之后，能沿用的上一轮审核一次用上。 */
export function adoptPrevious(id: string): Promise<{ confirmed: number; edited: number }> {
  return api(`/documents/${id}/adopt-previous`, { method: 'POST' })
}

/** 某一页的图片（PNG）。出处框的坐标就是这张图上的像素。 */
export function getPageImage(id: string, page: number): Promise<Blob> {
  return apiBlob(`/documents/${id}/pages/${page}/image`)
}

/** 框选建模板（ADR 0047）：框里的值、旁边的标签、在页面哪里、像什么类型。 */
export interface RegionDescription {
  text: string
  confidence: number | null
  label: string | null
  /** 值在标签的"右边"还是"下面"。 */
  side: string | null
  type: 'string' | 'number' | 'date' | 'date_range'
  position: string
  name: string
  description: string
}

export function describeRegion(id: string, page: number, box: Box): Promise<RegionDescription> {
  return api<RegionDescription>(`/documents/${id}/pages/${page}/describe-region`, {
    method: 'POST',
    json: { box },
  })
}

/** 某一页上一块区域放大后的图（ADR 0042）：候选值旁边显示，看清是哪个字。 */
export function getPageCrop(id: string, page: number, box: number[]): Promise<Blob> {
  return apiBlob(`/documents/${id}/pages/${page}/crop?box=${box.map((v) => v.toFixed(1)).join(',')}`)
}

/** 这张单据这一轮发给模型的单据文字原文（ADR 0053）。 */
export function getSentText(id: string, extractionId: string): Promise<SentText> {
  return api<SentText>(`/documents/${id}/extractions/${extractionId}/sent`)
}

/** 某一页的 OCR 结果：每个文字块的文字和位置。审核时"从原文选取"用。 */
export function getPageOcr(id: string, page: number): Promise<PageOcr> {
  return api<PageOcr>(`/documents/${id}/pages/${page}/ocr`)
}

/** 重新识别页面上的一块区域：裁下来、放大，再 OCR 一次。只返回文字，不保存。 */
/** 让模型看一块区域（ADR 0038）：读出文字和要填的字段的值。不保存。 */
export function lookRegion(
  id: string,
  page: number,
  body: { box: Box; label?: string; type?: string },
): Promise<{ text: string; value: string | null; model: string }> {
  return api(`/documents/${id}/pages/${page}/look-region`, { method: 'POST', json: body })
}

export function ocrRegion(id: string, page: number, box: Box): Promise<RegionOut> {
  return api<RegionOut>(`/documents/${id}/pages/${page}/ocr-region`, {
    method: 'POST',
    json: { box },
  })
}

/** 完成审核：所有标黄的字段都处理过之后，文档变成"已审核"。 */
export function completeReview(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/review`, { method: 'POST' })
}

/**
 * 导出方式（选了多个文档时）：
 * - separate：每个文档一个 Excel，打包成 zip，解压后是一个文件夹；
 * - summary：所有文档汇总到一个 Excel，一行一张单据，方便对账。
 * - csv：CSV（UTF-8），内容和汇总 Excel 一样；有明细表时每张表一个 .csv 打包成 zip。
 * - json：JSON（UTF-8），给别的程序读：文档 → 每张单据 → 字段、表格。
 */
export type ExportMode = 'separate' | 'summary' | 'csv' | 'json'

/** 导出：一个文档就是一个 Excel；多个文档按 mode 打包成 zip 或汇总成一个 Excel。 */
/** language：列名用哪种语言（ADR 0048），不写或 zh 是中文；模板没写这种语言的列还是中文。 */
export function exportDocuments(
  ids: string[],
  mode: ExportMode,
  language?: string,
): Promise<DownloadedFile> {
  const lang = language && language !== 'zh' ? language : null
  if (mode === 'csv' || mode === 'json') {
    const query = new URLSearchParams()
    for (const id of ids) query.append('ids', id)
    if (lang) query.append('lang', lang)
    return apiDownload(`/documents/export.${mode}?${query}`, `export.${mode}`)
  }
  if (ids.length === 1) {
    const one = lang ? `?${new URLSearchParams({ lang })}` : ''
    return apiDownload(`/documents/${ids[0]}/export.xlsx${one}`, 'export.xlsx')
  }
  const query = new URLSearchParams()
  for (const id of ids) {
    query.append('ids', id) // 后端要的格式：?ids=第一个&ids=第二个
  }
  if (lang) query.append('lang', lang)
  return mode === 'separate'
    ? apiDownload(`/documents/export.zip?${query}`, 'export.zip')
    : apiDownload(`/documents/export.xlsx?${query}`, 'export.xlsx')
}

/** 先跳过：拿不准的先放一放，不算进"下一份"。 */
export function skipDocument(id: string, reason: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/skip`, { method: 'POST', json: { reason } })
}

/** 取消跳过：回到待审核里。 */
export function unskipDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/unskip`, { method: 'POST' })
}

/** 一份文档的全部备注。 */
export function listComments(id: string): Promise<DocComment[]> {
  return api<DocComment[]>(`/documents/${id}/comments`)
}

/** 写备注。field 不写 = 整份文档；写在字段上要给第几页。 */
export function addComment(
  id: string,
  body: string,
  field?: { name: string; page: number },
): Promise<DocComment> {
  return api<DocComment>(`/documents/${id}/comments`, {
    method: 'POST',
    json: { body, field_name: field?.name ?? null, page: field?.page ?? null },
  })
}

export function resolveComment(commentId: string, resolved: boolean): Promise<DocComment> {
  return api<DocComment>(`/comments/${commentId}/${resolved ? 'resolve' : 'reopen'}`, {
    method: 'POST',
  })
}

export function deleteComment(commentId: string): Promise<void> {
  return api<void>(`/comments/${commentId}`, { method: 'DELETE' })
}

/** 某一页的结构：版面区域、表格的格子、标题 → 值（ADR 0034）。 */
export function getPageStructure(id: string, page: number, run?: number): Promise<PageStructure> {
  const query = run ? `?run=${run}` : ''
  return api<PageStructure>(`/documents/${id}/pages/${page}/structure${query}`)
}

/** 删除文档（一份或几份）：原始文件、处理结果、审核记录、备注都删掉，不能恢复。正在处理的跳过。 */
/** 批量重新处理的设置。null：每份和上次一样；template 不写：各自用现在这个模板的最新版。 */
export interface BatchReprocess {
  template?: string
  split: 'auto' | 'one' | 'pages' | null
  vision: 'default' | 'always' | 'off' | null
  pdf_read: 'default' | 'image' | 'text' | null
  reason: ReprocessReason
  note: string
}

/** 勾选的几份用同样的设置一起重新处理。skipped：没处理的（正在处理、还在排队……）。 */
export function reprocessDocuments(
  ids: string[],
  settings: BatchReprocess,
): Promise<{ queued: number; skipped: string[] }> {
  return api<{ queued: number; skipped: string[] }>('/documents/reprocess', {
    method: 'POST',
    json: { ids, ...settings },
  })
}

export function deleteDocuments(ids: string[]): Promise<{ deleted: number; skipped: string[] }> {
  return api<{ deleted: number; skipped: string[] }>('/documents/delete', {
    method: 'POST',
    json: { ids },
  })
}
