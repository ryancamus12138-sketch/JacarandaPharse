/** 系统信息：/api/v1/system。 */

import { api } from './client'
import type { SystemInfo } from './types'

/** 模型能不能看图（ADR 0038）。很快，只问一次模型服务。 */
export function getVision(): Promise<{ sees_images: boolean; mode: string; file: string | null }> {
  return api('/system/vision')
}

export function getSystemInfo(): Promise<SystemInfo> {
  return api<SystemInfo>('/system')
}
