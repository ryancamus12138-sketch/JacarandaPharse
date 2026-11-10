/** 文档相关的接口：/api/v1/documents。 */

import { api, apiBlob } from './client'
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
