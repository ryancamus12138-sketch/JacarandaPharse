import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import type { FieldValue } from '../../api/types'
import { addRow, changeRow } from '../../api/values'

export type RowChange =
  | { kind: 'add'; extractionId: string; table: string; after: number | null }
  | {
      kind: 'delete' | 'restore'
      extractionId: string
      table: string
      index: number
      reason: string
    }

/** 表格增删行。成功后重新取这个文档和文档列表（规则会按新的行重新检查）。 */
export function useRows(documentId: string) {
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: (change: RowChange): Promise<FieldValue[]> =>
      change.kind === 'add'
        ? addRow(change.extractionId, change.table, change.after)
        : changeRow(change.extractionId, change.table, change.index, change.kind, change.reason),
    onSuccess: (_, change) => {
      const done = { add: '已加一行', delete: '已删除这一行', restore: '已恢复这一行' }
      void message.success(done[change.kind])
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
}

export type Rows = ReturnType<typeof useRows>
