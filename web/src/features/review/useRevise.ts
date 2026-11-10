import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App } from 'antd'
import { ApiError } from '../../api/client'
import type { RevisionIn } from '../../api/types'
import { reviseValue } from '../../api/values'

export interface ReviseVars {
  valueId: string
  change: RevisionIn
}

/**
 * 修改或确认一个字段。成功后让三份数据重新取：
 * 这个文档（框的颜色、标签）、文档列表（"待确认"的数字）、这个字段的修改历史。
 */
export function useRevise(documentId: string) {
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  return useMutation({
    mutationFn: ({ valueId, change }: ReviseVars) => reviseValue(valueId, change),
    onSuccess: (_, { valueId, change }) => {
      void message.success(change.action === 'confirm' ? '已确认' : '已保存修改')
      void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['value-history', valueId] })
    },
    onError: (err) => {
      // 新值格式不对、和当前值一样、别人刚改过……后端都会说清楚原因
      void message.error(err instanceof ApiError ? err.message : String(err))
    },
  })
}

export type Revise = ReturnType<typeof useRevise>
