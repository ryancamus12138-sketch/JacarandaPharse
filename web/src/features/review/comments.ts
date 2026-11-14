import { useQuery } from '@tanstack/react-query'
import { listComments } from '../../api/documents'
import type { DocComment } from '../../api/types'

/** Comments of a document (shared by the comments button and the icons next to fields). */
export function useComments(id: string) {
  return useQuery({ queryKey: ['comments', id], queryFn: () => listComments(id) })
}

/** Unresolved comments per field on this page: field ID → count. */
export function openCommentsOnPage(
  comments: DocComment[] | undefined,
  page: number,
): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const c of comments ?? []) {
    if (c.field_name && c.page === page && c.resolved_at === null) {
      counts[c.field_name] = (counts[c.field_name] ?? 0) + 1
    }
  }
  return counts
}
