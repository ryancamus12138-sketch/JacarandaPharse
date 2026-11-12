/** 导出记录：/api/v1/exports。 */

import { api, apiDownload, type DownloadedFile } from './client'
import type { ExportPage } from './types'

export function listExports(limit: number, offset: number): Promise<ExportPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return api<ExportPage>(`/exports?${query}`)
}

/** 重新下载以前导出的文件（当时的内容）。 */
export function downloadExport(id: string, fallbackName: string): Promise<DownloadedFile> {
  return apiDownload(`/exports/${id}/file`, fallbackName)
}
