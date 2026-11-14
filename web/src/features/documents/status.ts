import type { DocumentStatus } from '../../api/types'

/** In-flight statuses: lists refresh periodically until they become done or failed. */
export const IN_PROGRESS: DocumentStatus[] = ['uploaded', 'processing']

/** Statuses with exportable results (same as the backend EXPORTABLE). */
export const EXPORTABLE: DocumentStatus[] = ['done', 'reviewed']
