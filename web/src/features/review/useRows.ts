import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import type { FieldValue } from '../../api/types'
import { addRow, changeRow } from '../../api/values'
import { useTranslation } from 'react-i18next'

export type RowChange =
  | { kind: 'add'; extractionId: string; table: string; after: number | null }
  | {
      kind: 'delete' | 'restore'
      extractionId: string
      table: string
      index: number
      reason: string
    }

/**
 * Add and remove table rows. Refetches the document and the document list on success (rules are re-evaluated
 * on the new rows).
 */
export function useRows(documentId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: (change: RowChange): Promise<FieldValue[]> =>
      change.kind === 'add'
        ? addRow(change.extractionId, change.table, change.after)
        : changeRow(change.extractionId, change.table, change.index, change.kind, change.reason),
    onSuccess: (_, change) => {
      const done = { add: t('已加一行'), delete: t('已删除这一行'), restore: t('已恢复这一行') }
      void message.success(done[change.kind])
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
}

export type Rows = ReturnType<typeof useRows>
