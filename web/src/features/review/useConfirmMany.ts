import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import { confirmValues } from '../../api/values'
import { useTranslation } from 'react-i18next'

/**
 * Bulk confirmation: a row, a whole table, or all flagged fields. Refetches the document and the document
 * list on success.
 */
export function useConfirmMany(documentId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: (ids: string[]) => confirmValues(ids),
    onSuccess: (result) => {
      void message.success(t('已确认 {{confirmed}} 个', { confirmed: result.confirmed }))
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
}
