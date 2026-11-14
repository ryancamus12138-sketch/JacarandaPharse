/**
 * Desktop app: /api/v1/desktop. The data folder (backups, restoring, moving it), restarting the background
 * services, and the system dialogs for choosing a folder or a backup file. Only the desktop app answers; the
 * service elsewhere answers 404.
 */

import { api } from './client'

export interface BackupFile {
  name: string
  path: string
  /** full: database, files and settings (.tar); database: automatic database backup (.db). */
  kind: 'full' | 'database'
  size: number
  /** Seconds since the epoch. */
  created: number
  /** A full backup that includes the model files. */
  models: boolean
}

export interface DataInfo {
  /** Data folder in use. */
  path: string
  /** The app's own place for the data. */
  default: string
  size: number
  backups: BackupFile[]
  last_auto_backup: number | null
}

export type JobKind = 'backup' | 'restore' | 'move' | 'restart'

export interface BackupResult {
  name: string
  path: string
  size: number
  documents: number
  files: number
  models: boolean
}

export interface RestoreResult {
  /** Where the data that was there before the restore was moved (never deleted). */
  aside: string
  kind: 'full' | 'database'
  /** Files that the restored database names but that are missing (database backups only). */
  warnings: string[]
  /** Models in use when the backup was made that are neither in it nor in the data folder. */
  models_missing?: string[]
}

export interface MoveResult {
  path: string
  size: number
}

export interface Job {
  kind: JobKind
  running: boolean
  stage: string
  done: number
  total: number
  started: number
  finished: number | null
  result: Record<string, unknown> | null
  error: string | null
}

export interface MoveCheck {
  target: string
  size: number
  free: number
}

export interface DataLocation {
  /** The data folder in use cannot be found (for example on a drive that is not connected). */
  unavailable: string | null
  default: string
}

export function getDataInfo(): Promise<DataInfo> {
  return api<DataInfo>('/desktop/data')
}

/** The current or last maintenance job; null when none has run since the app started. */
export function getJob(): Promise<Job | null> {
  return api<Job | null>('/desktop/job')
}

/** destination null: the backups folder in the data folder. */
export function startBackup(destination: string | null, models: boolean): Promise<Job> {
  return api<Job>('/desktop/backups', { method: 'POST', json: { destination, models } })
}

export function startRestore(path: string): Promise<Job> {
  return api<Job>('/desktop/restore', { method: 'POST', json: { path } })
}

/** parent null: back to the app's own place. */
export function checkMove(parent: string | null): Promise<MoveCheck> {
  return api<MoveCheck>('/desktop/move/check', { method: 'POST', json: { parent } })
}

export function startMove(parent: string | null): Promise<Job> {
  return api<Job>('/desktop/move', { method: 'POST', json: { parent } })
}

export function restartServices(): Promise<Job> {
  return api<Job>('/desktop/restart', { method: 'POST' })
}

/** System folder dialog; null when cancelled. */
export async function chooseFolder(purpose: 'watch' | 'backup' | 'move'): Promise<string | null> {
  return (await api<{ path: string | null }>('/desktop/choose-folder', { method: 'POST', json: { purpose } })).path
}

/** System file dialog for a backup file (.tar or .db); null when cancelled. */
export async function chooseBackupFile(): Promise<string | null> {
  return (await api<{ path: string | null }>('/desktop/choose-file', { method: 'POST' })).path
}

export function openFolder(which: 'data' | 'backups'): Promise<void> {
  return api<void>(`/desktop/open-folder?which=${which}`, { method: 'POST' })
}

export function getLocation(): Promise<DataLocation> {
  return api<DataLocation>('/desktop/location')
}

export function retryLocation(): Promise<DataLocation> {
  return api<DataLocation>('/desktop/location/retry', { method: 'POST' })
}

/** Stop looking for the moved data folder and start with what the app's own place holds. */
export function switchToDefaultLocation(): Promise<DataLocation> {
  return api<DataLocation>('/desktop/location/default', { method: 'POST' })
}
