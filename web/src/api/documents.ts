/** Documents API: /api/v1/documents. */

import { api, apiBlob, apiDownload, type DownloadedFile } from './client'
import type {
  Box,
  Dashboard,
  DocComment,
  DocumentDetail,
  DocumentFilters,
  DocumentNeighbors,
  DocumentPage,
  DocumentSummary,
  FolderCount,
  PageOcr,
  PageStructure,
  RegionOut,
  ReprocessReason,
  SentText,
} from './types'

export function listDocuments(
  limit: number,
  offset: number,
  filters: DocumentFilters = {},
): Promise<DocumentPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  for (const status of filters.status ?? []) {
    query.append('status', status)
  }
  if (filters.template) query.set('template', filters.template)
  if (filters.date_from) query.set('date_from', filters.date_from)
  if (filters.date_to) query.set('date_to', filters.date_to)
  if (filters.q?.trim()) query.set('q', filters.q.trim())
  if (filters.pending) query.set('pending', 'true')
  if (filters.value_q?.trim()) query.set('value_q', filters.value_q.trim())
  if (filters.field) query.set('field', filters.field)
  if (filters.skipped !== undefined) query.set('skipped', String(filters.skipped))
  if (filters.folder) query.set('folder', filters.folder)
  return api<DocumentPage>(`/documents?${query}`)
}

/**
 * Previous and next documents of the list, and the next document awaiting review. list is the list page's
 * query string (its filters), so paging walks the list the user came from.
 */
export function getNeighbors(id: string, list = ''): Promise<DocumentNeighbors> {
  return api<DocumentNeighbors>(`/documents/${id}/neighbors${list ? `?${list}` : ''}`)
}

/** Dashboard: statistics, processing queue, items needing attention. */
export function getDashboard(): Promise<Dashboard> {
  return api<Dashboard>('/dashboard')
}

/** Upload a file. Sent as FormData (like the file upload on /docs); the field must be named file. */
export function uploadDocument(
  file: File,
  template?: string,
  options: { folder?: string; split?: string; skipDuplicate?: boolean } = {},
): Promise<DocumentSummary> {
  const form = new FormData()
  form.append('file', file)
  if (template) form.append('template', template) // Id of one of my templates, or discover.
  if (options.folder) form.append('folder', options.folder)
  if (options.split) form.append('split', options.split)
  if (options.skipDuplicate) form.append('skip_duplicate', 'true')
  return api<DocumentSummary>('/documents', { method: 'POST', body: form })
}

/** Folders imported in batches. */
export function listFolders(): Promise<FolderCount[]> {
  return api<FolderCount[]>('/documents/folders')
}

/** Requeue (retry after failure). */
export function processDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/process`, { method: 'POST' })
}

/**
 * Stop processing a queued or running document. It becomes failed ("stopped manually") and can be
 * reprocessed; the worker gives the document up within about a second.
 */
export function stopDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/stop`, { method: 'POST' })
}

/** Document details: fields, evidence and rule results of every page. */
export function getDocument(id: string, run?: number): Promise<DocumentDetail> {
  return api<DocumentDetail>(run ? `/documents/${id}?run=${run}` : `/documents/${id}`)
}

/**
 * Reprocess with another template (a new run). Without template: the latest version of the current template.
 */
export function reprocessDocument(
  id: string,
  body: {
    template?: string
    reason: ReprocessReason
    note: string
    split?: string
    /**
     * Whether to show the page image to the model: always, off, default (system setting). Omitted: same as
     * last time.
     */
    vision?: 'always' | 'off' | 'default'
    /**
     * How to read PDFs: image renders pages for OCR, text reads only the text layer, default follows the
     * system setting.
     */
    pdf_read?: 'image' | 'text' | 'default'
  },
): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/reprocess`, { method: 'POST', json: body })
}

/**
 * Accept previous confirmations: after reprocessing, apply all reviews from the previous run that can be
 * carried over.
 */
export function adoptPrevious(id: string): Promise<{ confirmed: number; edited: number }> {
  return api(`/documents/${id}/adopt-previous`, { method: 'POST' })
}

/** Image (PNG) of a page. Evidence box coordinates are pixels of this image. */
export function getPageImage(id: string, page: number): Promise<Blob> {
  return apiBlob(`/documents/${id}/pages/${page}/image`)
}

/**
 * Template building by region selection: the value inside the box, the nearby label, its position on the page
 * and its likely type.
 */
export interface RegionDescription {
  text: string
  confidence: number | null
  label: string | null
  /** Whether the value is to the right of or below the label. */
  side: string | null
  type: 'string' | 'number' | 'date' | 'date_range'
  position: string
  name: string
  description: string
}

export function describeRegion(id: string, page: number, box: Box): Promise<RegionDescription> {
  return api<RegionDescription>(`/documents/${id}/pages/${page}/describe-region`, {
    method: 'POST',
    json: { box },
  })
}

/** Zoomed image of a region on a page, shown next to candidate values to reveal the exact characters. */
export function getPageCrop(id: string, page: number, box: number[]): Promise<Blob> {
  return apiBlob(`/documents/${id}/pages/${page}/crop?box=${box.map((v) => v.toFixed(1)).join(',')}`)
}

/** Document text sent to the model for this document in this run. */
export function getSentText(id: string, extractionId: string): Promise<SentText> {
  return api<SentText>(`/documents/${id}/extractions/${extractionId}/sent`)
}

/**
 * OCR result of a page: text and position of every text block. Used by "select from source text" during
 * review.
 */
export function getPageOcr(id: string, page: number): Promise<PageOcr> {
  return api<PageOcr>(`/documents/${id}/pages/${page}/ocr`)
}

/**
 * Re-recognize a region of the page: crop, enlarge and run OCR again. Returns text only and saves nothing.
 */
/** Ask the model about a region: reads the text and the value of the target field. Saves nothing. */
export function lookRegion(
  id: string,
  page: number,
  body: { box: Box; label?: string; type?: string },
): Promise<{ text: string; value: string | null; model: string }> {
  return api(`/documents/${id}/pages/${page}/look-region`, { method: 'POST', json: body })
}

export function ocrRegion(id: string, page: number, box: Box): Promise<RegionOut> {
  return api<RegionOut>(`/documents/${id}/pages/${page}/ocr-region`, {
    method: 'POST',
    json: { box },
  })
}

/** Complete review: once every flagged field has been handled, the document becomes reviewed. */
export function completeReview(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/review`, { method: 'POST' })
}

/**
 * Export mode (for several documents):
 * - separate: one workbook per document, packed into a zip that extracts to a folder;
 * - summary: all documents in one workbook, one row per document, for reconciliation;
 * - csv: CSV (UTF-8) with the same content as the summary workbook; with line-item tables, one .csv per table
 *   packed into a zip;
 * - json: JSON (UTF-8) for other programs: document → sub-documents → fields and tables.
 */
export type ExportMode = 'separate' | 'summary' | 'csv' | 'json'

/**
 * Export: one document yields one workbook; several documents are packed into a zip or summarized in one
 * workbook according to mode.
 */
/**
 * language: language of column headers; omitted means each template's primary language; columns without a
 * name in that language keep their primary language name.
 */
export function exportDocuments(
  ids: string[],
  mode: ExportMode,
  language?: string,
): Promise<DownloadedFile> {
  // Sent even for Chinese: the template's primary language may be another one.
  const lang = language || null
  if (mode === 'csv' || mode === 'json') {
    const query = new URLSearchParams()
    for (const id of ids) query.append('ids', id)
    if (lang) query.append('lang', lang)
    return apiDownload(`/documents/export.${mode}?${query}`, `export.${mode}`)
  }
  if (ids.length === 1) {
    const one = lang ? `?${new URLSearchParams({ lang })}` : ''
    return apiDownload(`/documents/${ids[0]}/export.xlsx${one}`, 'export.xlsx')
  }
  const query = new URLSearchParams()
  for (const id of ids) {
    query.append('ids', id) // Format expected by the backend: ?ids=FIRST&ids=SECOND
  }
  if (lang) query.append('lang', lang)
  return mode === 'separate'
    ? apiDownload(`/documents/export.zip?${query}`, 'export.zip')
    : apiDownload(`/documents/export.xlsx?${query}`, 'export.xlsx')
}

/** Skip for now: set aside a document that needs more thought; excluded from "Next". */
export function skipDocument(id: string, reason: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/skip`, { method: 'POST', json: { reason } })
}

/** Unskip: return the document to the review queue. */
export function unskipDocument(id: string): Promise<DocumentSummary> {
  return api<DocumentSummary>(`/documents/${id}/unskip`, { method: 'POST' })
}

/** All comments of a document. */
export function listComments(id: string): Promise<DocComment[]> {
  return api<DocComment[]>(`/documents/${id}/comments`)
}

/** Add a comment. Without field it applies to the whole document; field comments need the page. */
export function addComment(
  id: string,
  body: string,
  field?: { name: string; page: number },
): Promise<DocComment> {
  return api<DocComment>(`/documents/${id}/comments`, {
    method: 'POST',
    json: { body, field_name: field?.name ?? null, page: field?.page ?? null },
  })
}

export function resolveComment(commentId: string, resolved: boolean): Promise<DocComment> {
  return api<DocComment>(`/comments/${commentId}/${resolved ? 'resolve' : 'reopen'}`, {
    method: 'POST',
  })
}

export function deleteComment(commentId: string): Promise<void> {
  return api<void>(`/comments/${commentId}`, { method: 'DELETE' })
}

/** Structure of a page: layout regions, table cells, label → value pairs. */
export function getPageStructure(id: string, page: number, run?: number): Promise<PageStructure> {
  const query = run ? `?run=${run}` : ''
  return api<PageStructure>(`/documents/${id}/pages/${page}/structure${query}`)
}

/**
 * Settings for bulk reprocessing. null: same as last time for each document; without template: each
 * document's latest template version.
 */
export interface BatchReprocess {
  template?: string
  split: 'auto' | 'one' | 'pages' | null
  vision: 'default' | 'always' | 'off' | null
  pdf_read: 'default' | 'image' | 'text' | null
  reason: ReprocessReason
  note: string
}

/**
 * Reprocess the selected documents with the same settings. Queued and running documents are restarted.
 * skipped: documents that could not be reprocessed (missing original file, …).
 */
export function reprocessDocuments(
  ids: string[],
  settings: BatchReprocess,
): Promise<{ queued: number; skipped: string[] }> {
  return api<{ queued: number; skipped: string[] }>('/documents/reprocess', {
    method: 'POST',
    json: { ids, ...settings },
  })
}

/** Stop several queued or running documents (see stopDocument). */
export function stopDocuments(ids: string[]): Promise<{ stopped: number; skipped: string[] }> {
  return api<{ stopped: number; skipped: string[] }>('/documents/stop', { method: 'POST', json: { ids } })
}

/** Stop every queued and running document (the processing queue's "Stop all"). */
export function stopAllDocuments(): Promise<{ stopped: number; skipped: string[] }> {
  return api<{ stopped: number; skipped: string[] }>('/documents/stop-all', { method: 'POST' })
}

/**
 * Delete documents (one or several): original files, results, review records and comments are deleted
 * permanently. Queued and running documents are deleted too; their processing stops and writes nothing.
 */
export function deleteDocuments(ids: string[]): Promise<{ deleted: number; skipped: string[] }> {
  return api<{ deleted: number; skipped: string[] }>('/documents/delete', {
    method: 'POST',
    json: { ids },
  })
}
