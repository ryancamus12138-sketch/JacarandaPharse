import { DownloadOutlined, DownOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, type ButtonProps, Dropdown, Space } from 'antd'
import { ApiError } from '../../api/client'
import { type ExportMode, exportDocuments } from '../../api/documents'
import { saveFile } from '../../utils/download'

const MAX_EXPORT = 100 // 和后端一样：一次最多导出 100 个

interface Props {
  ids: string[]
  type?: ButtonProps['type']
}

/**
 * 导出按钮。
 * - 一个文档：直接下载这一份 Excel；
 * - 多个文档：主按钮下载 zip（每份一个 Excel），旁边的小箭头可以选"汇总到一个 Excel"。
 */
export function ExportButton({ ids, type }: Props) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const download = useMutation({
    mutationFn: (mode: ExportMode) => exportDocuments(ids, mode),
    onSuccess: ({ blob, filename }) => {
      saveFile(blob, filename)
      void message.success(`已导出 ${filename}（导出记录里可以重新下载）`)
      void queryClient.invalidateQueries({ queryKey: ['exports'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const tooMany = ids.length > MAX_EXPORT
  const disabled = ids.length === 0 || tooMany
  const title = tooMany ? `一次最多导出 ${MAX_EXPORT} 个` : undefined

  const csv = {
    key: 'csv',
    label: '导出 CSV（UTF-8，给别的系统用；有明细表时打包成 zip）',
  }

  if (ids.length <= 1) {
    return (
      <Space.Compact>
        <Button
          type={type}
          icon={<DownloadOutlined />}
          disabled={disabled}
          loading={download.isPending && download.variables === 'separate'}
          onClick={() => download.mutate('separate')}
        >
          导出 Excel
        </Button>
        <Dropdown disabled={disabled} menu={{ items: [csv], onClick: () => download.mutate('csv') }}>
          <Button
            type={type}
            icon={<DownOutlined />}
            aria-label="更多导出格式"
            loading={download.isPending && download.variables === 'csv'}
          />
        </Dropdown>
      </Space.Compact>
    )
  }

  return (
    // Space.Compact：两个按钮紧贴在一起，看起来像一个
    <Space.Compact>
      <Button
        type={type}
        icon={<DownloadOutlined />}
        disabled={disabled}
        title={title}
        loading={download.isPending && download.variables === 'separate'}
        onClick={() => download.mutate('separate')}
      >
        导出 {ids.length} 份（每份一个 Excel）
      </Button>
      <Dropdown
        disabled={disabled}
        menu={{
          items: [{ key: 'summary', label: '汇总到一个 Excel（一行一张单据，方便对账）' }, csv],
          onClick: ({ key }) => download.mutate(key === 'csv' ? 'csv' : 'summary'),
        }}
      >
        <Button
          type={type}
          icon={<DownOutlined />}
          aria-label="更多导出格式"
          loading={download.isPending && download.variables !== 'separate'}
        />
      </Dropdown>
    </Space.Compact>
  )
}
