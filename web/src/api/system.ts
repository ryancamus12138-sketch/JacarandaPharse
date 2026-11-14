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
