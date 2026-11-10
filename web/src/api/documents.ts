/** 文档相关的接口：/api/v1/documents。 */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'
import type { DocumentDetail, DocumentPage, DocumentSummary } from './types'

export function listDocuments(limit: number, offset: number): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return api<DocumentPage>(`/documents?${query}`)
}

/** 上传一个文件。文件用 FormData 发（和 /docs 里选文件上传一样），字段名必须叫 file。 */
export function uploadDocument(file: File): Promise<DocumentSummary> {
  const form = new FormData()
  form.append('file', file)
  return api<DocumentSummary>('/documents', { method: 'POST', body: form })
}

/** 重新处理（失败后重试）。 */
export function processDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/process`, { method: 'POST' })
}

/** 文档详情：每一页的字段、出处和规则校验结果。 */
export function getDocument(id: string): Promise<DocumentDetail> {
  return api<DocumentDetail>(`/documents/${id}`)
}

/** 某一页的图片（PNG）。出处框的坐标就是这张图上的像素。 */
export function getPageImage(id: string, page: number): Promise<Blob> {
  return apiBlob(`/documents/${id}/pages/${page}/image`)
}

/** 完成审核：所有标黄的字段都处理过之后，文档变成"已审核"。 */
export function completeReview(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/review`, { method: 'POST' })
}

/**
 * 导出方式（选了多个文档时）：
 * - separate：每个文档一个 Excel，打包成 zip，解压后是一个文件夹；
 * - summary：所有文档汇总到一个 Excel，一行一张单据，方便对账。
 */
export type ExportMode = 'separate' | 'summary'

/** 导出：一个文档就是一个 Excel；多个文档按 mode 打包成 zip 或汇总成一个 Excel。 */
export function exportDocuments(ids: string[], mode: ExportMode): Promise<DownloadedFile> {
  if (ids.length === 1) {
    return apiDownload(`/documents/${ids[0]}/export.xlsx`, 'export.xlsx')
  }
  const query = new URLSearchParams()
  for (const id of ids) {
    query.append('ids', id) // 后端要的格式：?ids=第一个&ids=第二个
  }
  return mode === 'separate'
    ? apiDownload(`/documents/export.zip?${query}`, 'export.zip')
    : apiDownload(`/documents/export.xlsx?${query}`, 'export.xlsx')
}
