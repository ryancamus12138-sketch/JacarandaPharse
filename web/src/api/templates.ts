/** 模板：/api/v1/templates（ADR 0030）。 */

import { api, apiDownload, type DownloadedFile } from './client'
import type {
  ExcelAnalysis,
  ExcelSheet,
  RuleInfo,
  SampleField,
  Template,
  TemplateIn,
  TrialResult,
} from './types'

/** 一个模板的概要（文档列表按模板筛选用）。 */
export interface TemplateSummary {
  name: string
  title: string
  version: number
  kind?: Template['kind']
}

/** 我的模板（没归档的在前）+ 内置模板。 */
export function listTemplates(): Promise<Template[]> {
  return api<Template[]>('/templates')
}

/** 起步模板库（九个）。 */
export function listStarters(): Promise<Template[]> {
  return api<Template[]>('/templates/starters')
}

export function listRules(): Promise<RuleInfo[]> {
  return api<RuleInfo[]>('/templates/rules')
}

/** 一个模板。我的模板带历史版本和"有几份文档在用"。 */
export function getTemplate(name: string): Promise<Template> {
  return api<Template>(`/templates/${name}`)
}

export function getTemplateVersion(id: string, version: number): Promise<Template> {
  return api<Template>(`/templates/${id}/versions/${version}`)
}

/** 新建：空白、从起步模板复制、或者复制我的另一个模板。 */
export function createTemplate(body: {
  source: 'blank' | 'starter' | 'copy'
  starter?: string
  copy_of?: string
  title?: string
  /** 空白模板：编辑页里填好的内容。 */
  content?: TemplateIn
}): Promise<Template> {
  return api<Template>('/templates', { method: 'POST', json: body })
}

/** 保存一个新版本。 */
export function saveTemplate(id: string, body: TemplateIn): Promise<Template> {
  return api<Template>(`/templates/${id}`, { method: 'PUT', json: body })
}

export function archiveTemplate(id: string, archived: boolean): Promise<Template> {
  return api<Template>(`/templates/${id}/archive`, { method: 'POST', json: { archived } })
}

/** 试跑：用最新版在一份文档上抽一次，不保存。要等模型跑完。 */
export function trialTemplate(id: string, documentId: string): Promise<TrialResult> {
  return api<TrialResult>(`/templates/${id}/trial`, {
    method: 'POST',
    json: { document_id: documentId },
  })
}

/** 用样例建模板：样例文档里选好的键值对 → 新模板；样例换成按新模板的结果。 */
export function createFromSample(body: {
  document_id: string
  title: string
  category: string
  description: string
  fields: SampleField[]
  rules: string[]
}): Promise<{ template: Template; document_id: string }> {
  return api('/templates/from-sample', { method: 'POST', json: body })
}

/** 导出模板：JSON 文件，可以在别的电脑上导入。name 是我的模板的 id，或者起步模板的名字。 */
export function exportTemplate(
  name: string,
  format: 'xlsx' | 'json' = 'xlsx',
): Promise<DownloadedFile> {
  return apiDownload(`/templates/${name}/export?format=${format}`, `template.${format}`)
}

/** 上传 Excel：标准模板 Excel 直接读出模板；业务报表返回每张 sheet 的猜测。 */
export function analyzeExcel(file: File): Promise<ExcelAnalysis> {
  const form = new FormData()
  form.append('file', file)
  return api<ExcelAnalysis>('/templates/excel', { method: 'POST', body: form })
}

/** 再看一次上传过的 Excel（刷新预览页时用）。 */
export function reanalyzeExcel(uploadId: string): Promise<ExcelAnalysis> {
  return api<ExcelAnalysis>(`/templates/excel/${uploadId}`)
}

/** 按预览里确认的结果建模板。 */
export function buildFromExcel(
  uploadId: string,
  body: { title: string; category: string; description: string; starter: string; sheets: ExcelSheet[] },
): Promise<Template> {
  return api<Template>(`/templates/excel/${uploadId}/build`, { method: 'POST', json: body })
}

/** 导入"导出模板"得到的 JSON 文件：新建一个我的模板。 */
export function importTemplate(file: unknown): Promise<Template> {
  return api<Template>('/templates/import', { method: 'POST', json: file })
}

/** 删除模板。有文档用过的不能删（只能归档）。 */
export function deleteTemplate(id: string): Promise<void> {
  return api<void>(`/templates/${id}`, { method: 'DELETE' })
}
