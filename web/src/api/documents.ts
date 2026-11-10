/** 文档相关的接口：/api/v1/documents。 */

import { api } from './client'
import type { DocumentPage, DocumentSummary } from './types'

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
