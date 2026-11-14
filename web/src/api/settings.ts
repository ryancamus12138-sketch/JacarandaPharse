/** Settings page: /api/v1/settings. */

import { api } from './client'

/** One folder watched for new files, with the template its files are processed with. */
export interface WatchFolder {
  path: string
  /** auto, discover, or a template id (the choices at upload). */
  template: string
}

export interface SettingItem {
  /** Matches the variable name in .env, lowercased. */
  key: string
  group: string
  label: string
  help: string
  kind: 'choice' | 'bool' | 'int' | 'path' | 'text' | 'template' | 'watch_folders'
  /** A list of folders for watch_folders. */
  value: string | number | boolean | WatchFolder[] | null
  default: string | number | boolean | WatchFolder[] | null
  /** env: set in .env (not editable on the page); page: set on the settings page; default: default value. */
  source: 'env' | 'page' | 'default'
  /** Variable name in .env. */
  env: string
  options: { value: string; label: string }[]
  min: number | null
  max: number | null
  /** Settings page that shows it: Recognition and processing, or Data and backups. */
  section: 'processing' | 'data'
  /** Applies without restarting the background services. */
  live: boolean
}

export interface SettingsOut {
  items: SettingItem[]
  can_change: boolean
  /** Changed on the page but `jacara stop` / `jacara start` has not run yet. */
  restart_needed: boolean
}

export function getSettings(): Promise<SettingsOut> {
  return api<SettingsOut>('/settings')
}

/** A null value restores the default. */
export function updateSettings(values: Record<string, unknown>): Promise<SettingsOut> {
  return api<SettingsOut>('/settings', { method: 'PUT', json: { values } })
}
