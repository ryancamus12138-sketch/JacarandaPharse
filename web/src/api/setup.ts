/**
 * First-start wizard of the desktop app: /api/v1/setup. Downloading the models listed with the program,
 * placing files in the inbox folder, or choosing a model file on this computer.
 */

import { api } from './client'

export interface SetupSource {
  id: string
  label: string
}

export interface ListedModel {
  filename: string
  slot: 'model' | 'mmproj'
  name: string
  size: number
  license: string
  default: boolean
  /** The model library already has this exact file. */
  in_library: boolean
}

export type ItemState = 'waiting' | 'downloading' | 'verifying' | 'importing' | 'done' | 'paused' | 'failed'

export interface SetupItem {
  filename: string
  slot: string
  origin: 'download' | 'inbox' | 'file'
  state: ItemState
  done: number
  total: number
  /** Bytes per second over the last few seconds. */
  speed: number
  /** Seconds left at the current speed. */
  eta: number | null
  source: string | null
  /** Sources given up for this file, with the reason. */
  switched: { source: string; label: string; reason: string }[]
  error: string | null
  /** Model library version once imported ("name@v2"). */
  imported: string | null
  /** A chosen file not in the list, imported as it is. */
  custom: boolean
  /** Inbox file: its path relative to the models folder. */
  rel: string | null
}

export interface InboxFile {
  /** Path relative to the models folder (inbox/… or a loose file in the models folder). */
  name: string
  size: number
  expected: number | null
  /**
   * copying: smaller than listed or still changing; waiting: complete, after the file being worked on;
   * verifying / importing: being checked / put into the model library (done, total); in_library: the
   * library already has the listed file; busy: the same file is being downloaded or copied another way.
   */
  status:
    | 'copying'
    | 'waiting'
    | 'verifying'
    | 'importing'
    | 'in_library'
    | 'same_size_in_library'
    | 'busy'
    | 'wrong_size'
    | 'failed'
    | 'unknown'
    | 'shards_missing'
    | 'folder_link'
  /** Progress (bytes) while verifying or importing. */
  done: number | null
  total: number | null
  error: string | null
  /** Model library kind it is taken for, or suggested (null: the user chooses). */
  kind: string | null
  /** Can be imported with importInboxFile. */
  importable: boolean
  /** Split GGUF model: files found and files it has. */
  parts: number | null
  count: number | null
  /** .txt files next to an .onnx file: recognition dictionaries to choose from. */
  dictionaries: string[]
  /** Model library name suggested for the import. */
  suggested: string | null
  /** Can be deleted with deleteInboxFiles (not while it is being worked on). */
  deletable: boolean
  /** A symbolic link: deleting it leaves the file it points to. */
  link: boolean
  /** For a link: the file or folder it points to. */
  target: string | null
  /** Outside the inbox, where earlier versions kept models: not used by the desktop app; import or delete it. */
  legacy: boolean
}

export type ModelKind = 'model' | 'mmproj' | 'ocr_det' | 'ocr_rec' | 'table'

export interface SetupState {
  /** No large model is found. */
  needs_model: boolean
  /** Model file set with JACARA_MODEL that does not exist (it takes precedence over the app's choice). */
  model_missing: string | null
  model: string | null
  mmproj: string | null
  files: ListedModel[]
  sources: SetupSource[]
  pinned: string | null
  preferred: string | null
  items: SetupItem[]
  running: boolean
  inbox: string
  inbox_files: InboxFile[]
  /** Other files in the inbox (not model files): only counted. */
  inbox_other: number
  /** Names of the model library kinds. */
  kinds: Record<string, string>
  /** Free space on the disk of the data folder (bytes). */
  free: number
}

/** inbox: also check the inbox folder (and import complete listed files). */
export function getSetup(inbox = false): Promise<SetupState> {
  return api<SetupState>(`/setup${inbox ? '?inbox=true' : ''}`)
}

/** source: a source chosen under Advanced; null for automatic. */
export function startDownloads(files: string[], source: string | null): Promise<void> {
  return api<void>('/setup/downloads', { method: 'POST', json: { files, source } })
}

export function pauseDownloads(): Promise<void> {
  return api<void>('/setup/pause', { method: 'POST' })
}

export function resumeDownloads(): Promise<void> {
  return api<void>('/setup/resume', { method: 'POST' })
}

/** Stop and delete the partial files. */
export function cancelDownloads(): Promise<void> {
  return api<void>('/setup/cancel', { method: 'POST' })
}

export function openInbox(): Promise<void> {
  return api<void>('/setup/open-inbox', { method: 'POST' })
}

/** System file dialog for a .gguf file; null when cancelled. */
export async function chooseModelFile(): Promise<string | null> {
  return (await api<{ path: string | null }>('/setup/choose-file', { method: 'POST' })).path
}

/**
 * Import a file shown in the inbox list as the chosen kind. .gguf files are put to use; OCR models only go
 * into the model library. modelName is ignored for a listed file.
 */
export function importInboxFile(name: string, kind: ModelKind, modelName: string, dictionary: string | null): Promise<void> {
  return api<void>('/setup/inbox/import', {
    method: 'POST',
    json: { name, kind, model_name: modelName, dictionary },
  })
}

/** Delete files shown in the inbox list, permanently (a link: only the link; a split model: all its files). */
export async function deleteInboxFiles(names: string[]): Promise<string[]> {
  return (await api<{ deleted: string[] }>('/setup/inbox/delete', { method: 'POST', json: { names } })).deleted
}

/** Copy a model file on this computer into the model library and use it. */
export function importModelFile(path: string): Promise<void> {
  return api<void>('/setup/files', { method: 'POST', json: { path } })
}
