/** Export history: /api/v1/exports. */

import { api, apiDownload, type DownloadedFile } from './client'
import type { ExportPage } from './types'

export function listExports(limit: number, offset: number): Promise<ExportPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return api<ExportPage>(`/exports?${query}`)
}

/** Download a past export again (content as exported at the time). */
export function downloadExport(id: string, fallbackName: string): Promise<DownloadedFile> {
  return apiDownload(`/exports/${id}/file`, fallbackName)
}
