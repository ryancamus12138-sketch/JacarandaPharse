/**
 * Router state passed from the document list to a document page: the list's query string (its filters), so
 * previous / next on the review page walk the same list and "back to list" returns to it with its filters.
 * Kept in history state rather than the document URL, which stays a plain link to the document.
 */
export interface ListState {
  list: string
}

/** The list query string carried in router state; empty (the whole list) when the page was opened otherwise. */
export function listOf(state: unknown): string {
  if (typeof state === 'object' && state !== null && 'list' in state) {
    const list = (state as { list: unknown }).list
    if (typeof list === 'string') return list
  }
  return ''
}
