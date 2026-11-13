/** 换模型：/api/v1/models（ADR 0046）。 */

import { api } from './client'

export interface ModelFile {
  /** 相对项目根目录，例如 models/Qwen3.5-9B-Q4_K_M.gguf。 */
  path: string
  name: string
  size: number
  modified: string
}

export interface ModelSlot {
  key: 'model' | 'mmproj' | 'ocr_det' | 'ocr_rec' | 'ocr_dict' | 'table'
  label: string
  current: string | null
  /** env：.env 指定；choice：网页上选的；auto：自动找到的；builtin：自带的；none：没有。 */
  source: 'env' | 'choice' | 'auto' | 'builtin' | 'none'
  env: string
  files: ModelFile[]
}

export interface ModelsInfo {
  slots: ModelSlot[]
  /** 模型服务现在加载的大模型文件名。 */
  loaded: string | null
  restart_needed: boolean
}

export function getModels(): Promise<ModelsInfo> {
  return api<ModelsInfo>('/models')
}

/** 值是 models/ 里的文件；空字符串：不用（视觉模型）或用自带的（OCR、表格）。 */
export function chooseModels(body: Partial<Record<ModelSlot['key'], string>>): Promise<ModelsInfo> {
  return api<ModelsInfo>('/models', { method: 'PUT', json: body })
}
