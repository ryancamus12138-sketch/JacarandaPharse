import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import { confirmValues } from '../../api/values'

/** 批量确认：一整行、整张表或全部标黄的。成功后重新取这个文档和文档列表。 */
export function useConfirmMany(documentId: string) {
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: (ids: string[]) => confirmValues(ids),
    onSuccess: (result) => {
      void message.success(`已确认 ${result.confirmed} 个`)
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
}
