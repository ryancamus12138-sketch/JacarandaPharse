import { SwapOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Select, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { chooseModels, getModels, type ModelSlot } from '../../api/models'
import { useAuth } from '../../auth/useAuth'
import { formatBytes } from '../../utils/format'

const SOURCE: Record<ModelSlot['source'], { text: string; color: string }> = {
  env: { text: '.env 指定', color: 'blue' },
  choice: { text: '网页上选的', color: 'purple' },
  auto: { text: '自动找到', color: 'default' },
  builtin: { text: 'RapidOCR 自带', color: 'default' },
  none: { text: '没有', color: 'warning' },
}

/** 每一项的"不用"选项怎么叫（大模型不能不用）。 */
const EMPTY: Partial<Record<ModelSlot['key'], string>> = {
  mmproj: '不用视觉模型',
  ocr_det: '用 RapidOCR 自带的',
  ocr_rec: '用 RapidOCR 自带的',
  ocr_dict: '用 RapidOCR 自带的',
  table: '不用（按文字位置划分格子）',
}

/**
 * 换模型（ADR 0046）：只能选 models/ 文件夹里已有的文件。放新模型：把文件拷进 models/（OCR 模型放
 * models/ocr/，表格模型放 models/table/），刷新这一页就能选。改完 jacara stop、jacara start 才生效。
 */
export function ModelsCard() {
  const { message } = App.useApp()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const models = useQuery({ queryKey: ['models'], queryFn: getModels })
  const [draft, setDraft] = useState<Partial<Record<ModelSlot['key'], string>>>({})
  const save = useMutation({
    mutationFn: () => chooseModels(draft),
    onSuccess: (data) => {
      queryClient.setQueryData(['models'], data)
      setDraft({})
      void message.success('已保存。在终端里 jacara stop、再 jacara start 后生效')
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const editable = can('system:manage')
  const changed = Object.keys(draft).length > 0

  const columns: TableProps<ModelSlot>['columns'] = [
    { title: '', dataIndex: 'label', width: 130 },
    {
      title: '现在用的',
      render: (_, s) => {
        const value = draft[s.key] ?? s.current ?? ''
        const options = [
          ...(EMPTY[s.key] !== undefined ? [{ value: '', label: EMPTY[s.key] }] : []),
          ...s.files.map((f) => ({ value: f.path, label: `${f.name}（${formatBytes(f.size)}）` })),
        ]
        if (!editable || s.source === 'env') {
          return (
            <Typography.Text>
              {s.current ? s.current.split('/').pop() : (EMPTY[s.key] ?? '没有')}
            </Typography.Text>
          )
        }
        return (
          <Select
            style={{ width: '100%', maxWidth: 420 }}
            value={value}
            options={options}
            placeholder={s.files.length ? '选一个文件' : 'models/ 里没有可选的文件'}
            onChange={(v: string) => setDraft((d) => ({ ...d, [s.key]: v }))}
          />
        )
      },
    },
    {
      title: '谁定的',
      width: 130,
      render: (_, s) => (
        <Tag color={SOURCE[s.source].color} title={s.source === 'env' ? `在 .env 里改 ${s.env}` : undefined}>
          {SOURCE[s.source].text}
        </Tag>
      ),
    },
  ]

  return (
    <Card
      title="换模型"
      loading={models.isPending}
      extra={
        editable && (
          <Button type="primary" icon={<SwapOutlined />} disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>
            保存
          </Button>
        )
      }
    >
      <Flex vertical gap={12}>
        {models.data?.restart_needed && (
          <Alert
            type="warning"
            showIcon
            title={`选的大模型和正在用的（${models.data.loaded}）不一样：在终端里 jacara stop、再 jacara start 才会换过来`}
          />
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          只能选 models/ 文件夹里已有的文件（离线，不会下载）：大模型、视觉模型放 models/，自己训练的 OCR 模型放
          models/ocr/（文件名里带 det、rec，字典是 .txt），表格模型放 models/table/。.env 里写了的优先，这里改不了。
          改完要重启 jacara 才生效。
        </Typography.Text>
        <Table rowKey="key" size="small" pagination={false} columns={columns} dataSource={models.data?.slots ?? []} />
      </Flex>
    </Card>
  )
}
