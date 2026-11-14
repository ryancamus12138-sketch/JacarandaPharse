/** Model and dataset library (/api/v1/hub) and OCR model evaluation. */

import { api, apiDownload, ApiError, type DownloadedFile } from './client'
import { getToken } from './token'
import { t, uiLanguage } from '../i18n'

export type ModelKind = 'model' | 'mmproj' | 'ocr_det' | 'ocr_rec' | 'table'
export type DatasetKind = 'ocr_rec' | 'ocr_det'
export type DatasetSplit = 'train' | 'val' | 'test' | 'all'

export interface ModelFile {
  name: string
  size: number
  sha256: string
  /** weights or dict (recognition dictionary). */
  role: 'weights' | 'dict'
}

export interface ModelVersion {
  version: number
  kind: ModelKind
  files: ModelFile[]
  /** Path of the weights file relative to the project root. */
  path: string
  base_model: string | null
  datasets: string[]
  license: string | null
  imported_at: string | null
  imported_by: string | null
  description: string
  /** Results of import validation: format, shapes, dictionary size, class count, … */
  checks: Record<string, unknown>
  in_use: boolean
}

export interface HubModel {
  name: string
  kind: ModelKind
  kind_label: string
  versions: ModelVersion[]
}

export interface HubModels {
  models: HubModel[]
  kinds: Record<ModelKind, string>
  /** Slots fixed in .env; these cannot be changed in the web UI. */
  env_locked: string[]
  restart_hint: string
}

export interface UploadedFile {
  name: string
  size: number
  sha256: string
}

export interface FinishModel {
  name: string
  kind?: ModelKind
  weights?: string
  dict_file?: string
  /** A zip exported from the library: kind, weights and dict_file are read from its model card. */
  archive?: string
  base_model?: string
  datasets?: string[]
  license?: string
  description?: string
}

export interface Dataset {
  id: string
  name: string
  version: number
  kind: DatasetKind
  /** samples (OCR sample export), det_pages (detection samples), upload (uploaded). */
  source: 'samples' | 'det_pages' | 'upload'
  description: string
  sha256: string
  size: number
  counts: Record<DatasetSplit, number>
  groups: Partial<Record<DatasetSplit, number>>
  files: { file: string; split: string }[]
  problems: Record<string, number>
  created_at: string
}

export interface ModelRef {
  source: 'current' | 'builtin' | 'hub'
  name?: string
  version?: number
}

export interface EvalArm {
  name: 'A' | 'B'
  label: string
  det: string | null
  rec: string | null
  dict: string | null
  sha256: Record<string, string>
}

export interface RecSide {
  lines: number
  exact: number
  exact_match: number
  char_match: number
  format_only: number
  empty: number
}

export interface DetSide {
  images: number
  gt_boxes: number
  det_boxes: number
  matched: number
  precision: number
  recall: number
  hmean: number
}

export interface EvalSummary {
  task: 'rec' | 'det'
  a: RecSide & DetSide
  b: RecSide & DetSide
  fixed: number
  broken: number
  mcnemar_p: number
  bootstrap_ci?: [number, number] | null
  groups: number
  grouped?: boolean
  evidence: 'process' | 'thin' | 'trend'
  evidence_text: string
  seconds: number
}

export interface EvalRun {
  id: string
  dataset_id: string
  dataset: string
  split: string
  task: 'rec' | 'det'
  arms: EvalArm[]
  status: 'queued' | 'running' | 'done' | 'failed'
  progress: string
  summary: EvalSummary | null
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export const KIND_SUFFIX: Record<ModelKind, string> = {
  model: '.gguf',
  mmproj: '.gguf',
  ocr_det: '.onnx',
  ocr_rec: '.onnx',
  table: '.onnx',
}

export const SPLIT_NAMES: Record<DatasetSplit, string> = {
  get train() { return t('训练集') },
  get val() { return t('验证集') },
  get test() { return t('锁定测试集') },
  get all() { return t('未划分') },
}

export const SOURCE_NAMES: Record<Dataset['source'], string> = {
  get samples() { return t('OCR 样本') },
  get det_pages() { return t('检测样本') },
  get upload() { return t('上传') },
}

export function listModels(): Promise<HubModels> {
  return api<HubModels>('/hub/models')
}

export function startModelUpload(): Promise<{ upload_id: string }> {
  return api('/hub/models/uploads', { method: 'POST' })
}

/**
 * Upload one model file. The request body is the file itself (no multipart), so multi-gigabyte files work.
 * Uses XMLHttpRequest rather than fetch because fetch cannot report upload progress.
 */
export function uploadModelFile(
  uploadId: string,
  file: File,
  onProgress: (percent: number) => void,
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', `/api/v1/hub/models/uploads/${uploadId}/files/${encodeURIComponent(file.name)}`)
    const token = getToken()
    if (token) {
      xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    }
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.setRequestHeader('Accept-Language', uiLanguage())
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      let body: { detail?: unknown } = {}
      try {
        body = JSON.parse(xhr.responseText) as { detail?: unknown }
      } catch {
        // Not JSON: report the status code.
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as unknown as UploadedFile)
      } else {
        const detail = typeof body.detail === 'string' ? body.detail : t('上传失败（{{status}}）', { status: xhr.status })
        reject(new ApiError(xhr.status, detail))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, t('无法连接后端，上传已中断')))
    xhr.send(file)
  })
}

export function finishModelUpload(uploadId: string, data: FinishModel): Promise<ModelVersion> {
  return api<ModelVersion>(`/hub/models/uploads/${uploadId}/finish`, { method: 'POST', json: data })
}

export function cancelModelUpload(uploadId: string): Promise<void> {
  return api<void>(`/hub/models/uploads/${uploadId}`, { method: 'DELETE' })
}

export function activateModelVersion(name: string, version: number): Promise<HubModels> {
  return api<HubModels>(`/hub/models/${encodeURIComponent(name)}/versions/${version}/use`, { method: 'POST' })
}

export function updateModelCard(
  name: string,
  version: number,
  change: { base_model?: string; datasets?: string[]; license?: string; description?: string },
): Promise<ModelVersion> {
  return api<ModelVersion>(`/hub/models/${encodeURIComponent(name)}/versions/${version}`, {
    method: 'PATCH',
    json: change,
  })
}

export function deleteModelVersion(name: string, version: number): Promise<void> {
  return api<void>(`/hub/models/${encodeURIComponent(name)}/versions/${version}`, { method: 'DELETE' })
}

export function downloadModelVersion(name: string, version: number): Promise<DownloadedFile> {
  return apiDownload(`/hub/models/${encodeURIComponent(name)}/versions/${version}/download`, `${name}-v${version}.zip`)
}

export function listDatasets(): Promise<Dataset[]> {
  return api<Dataset[]>('/hub/datasets')
}

export function importDataset(file: File, name: string, kind: DatasetKind, description: string): Promise<Dataset> {
  const form = new FormData()
  form.append('file', file)
  form.append('name', name)
  form.append('kind', kind)
  form.append('description', description)
  return api<Dataset>('/hub/datasets', { method: 'POST', body: form })
}

export function downloadDataset(dataset: Dataset): Promise<DownloadedFile> {
  return apiDownload(`/hub/datasets/${dataset.id}/download`, `${dataset.name}-v${dataset.version}.zip`)
}

export function deleteDataset(id: string): Promise<void> {
  return api<void>(`/hub/datasets/${id}`, { method: 'DELETE' })
}

export function listEvals(): Promise<EvalRun[]> {
  return api<EvalRun[]>('/hub/evals')
}

export function createEval(data: { dataset_id: string; split: DatasetSplit; a: ModelRef; b: ModelRef }): Promise<EvalRun> {
  return api<EvalRun>('/hub/evals', { method: 'POST', json: data })
}

export function downloadEvalLines(id: string): Promise<DownloadedFile> {
  return apiDownload(`/hub/evals/${id}/lines`, t('OCR评测.jsonl'))
}
