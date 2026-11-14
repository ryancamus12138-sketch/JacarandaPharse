/** 设置页：/api/v1/settings（ADR 0063）。 */

import { api } from './client'

export interface SettingItem {
  /** 和 .env 里的变量名对应（小写）。 */
  key: string
  group: string
  label: string
  help: string
  kind: 'choice' | 'bool' | 'int' | 'path' | 'text'
  value: string | number | boolean | null
  default: string | number | boolean | null
  /** env：.env 指定（页面上改不了）；page：设置页上改的；default：默认值。 */
  source: 'env' | 'page' | 'default'
  /** .env 里的变量名。 */
  env: string
  options: { value: string; label: string }[]
  min: number | null
  max: number | null
}

export interface SettingsOut {
  items: SettingItem[]
  can_change: boolean
  /** 页面上改过、还没 jacara stop、jacara start。 */
  restart_needed: boolean
}

export function getSettings(): Promise<SettingsOut> {
  return api<SettingsOut>('/settings')
}

/** 值写 null 改回默认。 */
export function updateSettings(values: Record<string, unknown>): Promise<SettingsOut> {
  return api<SettingsOut>('/settings', { method: 'PUT', json: { values } })
}
