/** Templates: /api/v1/templates. */

import { api, apiDownload, type DownloadedFile } from './client'
import type {
  ExcelAnalysis,
  ExcelSheet,
  RuleInfo,
  SampleField,
  Template,
  TemplateField,
  TemplateIn,
  TemplateTable,
  TrialResult,
} from './types'

/** Template summary (for filtering the document list by template). */
export interface TemplateSummary {
  name: string
  title: string
  version: number
  kind?: Template['kind']
}

/** My templates (non-archived first) + built-in templates. */
export function listTemplates(): Promise<Template[]> {
  return api<Template[]>('/templates')
}

/** Starter template library (nine templates). */
export function listStarters(): Promise<Template[]> {
  return api<Template[]>('/templates/starters')
}

export function listRules(): Promise<RuleInfo[]> {
  return api<RuleInfo[]>('/templates/rules')
}

export interface FormulaCheck {
  ok: boolean
  /** Form with display names: Check-out date − Check-in date. */
  readable: string
  message: string
}

/**
 * Live validation of a formula or rule condition (using the fields currently in the editor; nothing is
 * saved).
 */
export function checkFormula(body: {
  text: string
  kind: 'formula' | 'condition'
  fields: TemplateField[]
  tables: TemplateTable[]
  row: string | null
}): Promise<FormulaCheck> {
  return api<FormulaCheck>('/templates/formula-check', { method: 'POST', json: body })
}

/** One template. My templates include version history and the number of documents using them. */
export function getTemplate(name: string): Promise<Template> {
  return api<Template>(`/templates/${name}`)
}

export function getTemplateVersion(id: string, version: number): Promise<Template> {
  return api<Template>(`/templates/${id}/versions/${version}`)
}

/** Create: blank, a copy of a starter template, or a copy of another of my templates. */
export function createTemplate(body: {
  source: 'blank' | 'starter' | 'copy'
  starter?: string
  copy_of?: string
  title?: string
  /** Blank template: the content entered in the editor. */
  content?: TemplateIn
}): Promise<Template> {
  return api<Template>('/templates', { method: 'POST', json: body })
}

/** Save a new version. */
export function saveTemplate(id: string, body: TemplateIn): Promise<Template> {
  return api<Template>(`/templates/${id}`, { method: 'PUT', json: body })
}

export function archiveTemplate(id: string, archived: boolean): Promise<Template> {
  return api<Template>(`/templates/${id}/archive`, { method: 'POST', json: { archived } })
}

/** Trial run: extract one document with the latest version without saving. Waits for the model. */
export function trialTemplate(id: string, documentId: string): Promise<TrialResult> {
  return api<TrialResult>(`/templates/${id}/trial`, {
    method: 'POST',
    json: { document_id: documentId },
  })
}

/**
 * Create a template from a sample: selected key-value pairs → new template; the sample is reprocessed with
 * it.
 */
/** Create a template by selecting regions on a sample: selected fields → new template. */
export function createFromBoxes(body: {
  document_id: string
  title: string
  category: string
  description: string
  fields: (TemplateField & { page: number; box: number[] })[]
}): Promise<Template> {
  return api<Template>('/templates/from-boxes', { method: 'POST', json: body })
}

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

/**
 * Export a template as a JSON file for import on another machine. name is the id of one of my templates, or
 * the name of a starter template.
 */
export function exportTemplate(
  name: string,
  format: 'xlsx' | 'json' = 'xlsx',
): Promise<DownloadedFile> {
  return apiDownload(`/templates/${name}/export?format=${format}`, `template.${format}`)
}

/**
 * Upload a workbook: a standard template workbook is read directly; a business report returns a guess for
 * each sheet.
 */
export function analyzeExcel(file: File): Promise<ExcelAnalysis> {
  const form = new FormData()
  form.append('file', file)
  return api<ExcelAnalysis>('/templates/excel', { method: 'POST', body: form })
}

/** Show a previously uploaded workbook again (when the preview page is reloaded). */
export function reanalyzeExcel(uploadId: string): Promise<ExcelAnalysis> {
  return api<ExcelAnalysis>(`/templates/excel/${uploadId}`)
}

/** Create a template from the confirmed preview. */
export function buildFromExcel(
  uploadId: string,
  body: { title: string; category: string; description: string; starter: string; sheets: ExcelSheet[] },
): Promise<Template> {
  return api<Template>(`/templates/excel/${uploadId}/build`, { method: 'POST', json: body })
}

/** Import a JSON file produced by "Export template" as a new template of mine. */
export function importTemplate(file: unknown): Promise<Template> {
  return api<Template>('/templates/import', { method: 'POST', json: file })
}

/** Delete a template. Templates used by documents are removed from lists but kept for those documents. */
export function deleteTemplate(id: string): Promise<void> {
  return api<void>(`/templates/${id}`, { method: 'DELETE' })
}

/** Change only the template's tags (domains, country or region, custom tags); no new version. */
export function setTemplateLabels(
  id: string,
  body: { domains?: string[]; countries?: string[]; tags?: string[] },
): Promise<Template> {
  return api<Template>(`/templates/${id}/labels`, { method: 'PATCH', json: body })
}
