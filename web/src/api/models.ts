/** Model selection: /api/v1/models. */

import { api } from './client'

export interface ModelFile {
  /** Relative to the project root, for example models/Qwen3.5-9B-Q4_K_M.gguf. */
  path: string
  name: string
  size: number
  modified: string
}

export interface ModelSlot {
  key: 'model' | 'mmproj' | 'ocr_det' | 'ocr_rec' | 'ocr_dict' | 'table'
  label: string
  current: string | null
  /** env: set in .env; choice: selected in the web UI; auto: detected; builtin: bundled; none: none. */
  source: 'env' | 'choice' | 'auto' | 'builtin' | 'bundled' | 'none'
  env: string
  files: ModelFile[]
}

export interface ModelsInfo {
  slots: ModelSlot[]
  /** LLM file currently loaded by the model server. */
  loaded: string | null
  restart_needed: boolean
}

export function getModels(): Promise<ModelsInfo> {
  return api<ModelsInfo>('/models')
}

/**
 * Values are files in models/; an empty string disables the slot (vision projector) or uses the bundled model
 * (OCR, table).
 */
export function chooseModels(body: Partial<Record<ModelSlot['key'], string>>): Promise<ModelsInfo> {
  return api<ModelsInfo>('/models', { method: 'PUT', json: body })
}
