/** System information: /api/v1/system. */

import { api } from './client'
import type { SystemInfo } from './types'

/** Whether the model can process images. Fast: queries the model server once. */
export function getVision(): Promise<{ sees_images: boolean; mode: string; file: string | null }> {
  return api('/system/vision')
}

export function getSystemInfo(): Promise<SystemInfo> {
  return api<SystemInfo>('/system')
}

/** Text for a problem report (administrators): versions, system, models, processes, checks; no document content. */
export async function getDiagnostics(): Promise<string> {
  return (await api<{ text: string }>('/system/diagnostics')).text
}

/** LICENSE, NOTICE or the third-party license list shipped with the program. */
export async function getLicenseText(which: 'license' | 'notice' | 'third_party'): Promise<string> {
  return (await api<{ text: string }>(`/system/licenses/${which}`)).text
}
