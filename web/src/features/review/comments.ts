import { useQuery } from '@tanstack/react-query'
import { listComments } from '../../api/documents'
import type { DocComment } from '../../api/types'

/** 一份文档的备注（备注按钮和字段旁边的小图标共用一份）。 */
export function useComments(id: string) {
  return useQuery({ queryKey: ['comments', id], queryFn: () => listComments(id) })
}

/** 这一页每个字段还没解决的备注几条：字段 ID → 条数。 */
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
