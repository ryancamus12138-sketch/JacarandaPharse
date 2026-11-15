import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import type { RevisionIn } from '../../api/types'
import { reviseValue } from '../../api/values'
import { useTranslation } from 'react-i18next'

export interface ReviseVars {
  valueId: string
  change: RevisionIn
}

/**
 * Edit or confirm a field. On success three queries are refetched: the document (box colors, tags), the
 * document list (pending counts), and the field's history.
 */
export function useRevise(documentId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: ({ valueId, change }: ReviseVars) => reviseValue(valueId, change),
    onSuccess: (_, { valueId, change }) => {
      void message.success(
        change.action === 'confirm'
          ? t('已确认')
          : change.source_lines
            ? t('已从原文填入')
            : t('已保存修改'),
      )
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      void queryClient.invalidateQueries({ queryKey: ['value-history', valueId] })
    },
    onError: (err) => {
      // Invalid format, unchanged value, a concurrent edit: the backend explains the cause.
      void message.error(err instanceof ApiError ? err.message : String(err))
    },
  })
}

export type Revise = ReturnType<typeof useRevise>
