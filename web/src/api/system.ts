/** 系统信息：/api/v1/system。 */

import { api } from './client'
import type { SystemInfo } from './types'

export function getSystemInfo(): Promise<SystemInfo> {
  return api<SystemInfo>('/system')
}
