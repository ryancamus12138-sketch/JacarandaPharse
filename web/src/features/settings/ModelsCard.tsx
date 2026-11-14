import { SwapOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Select, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { chooseModels, getModels, type ModelSlot } from '../../api/models'
import { useAuth } from '../../auth/useAuth'
import { formatBytes } from '../../utils/format'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const SOURCE: Record<ModelSlot['source'], { text: string; color: string }> = {
  env: { get text() { return t('.env 指定') }, color: 'blue' },
  choice: { get text() { return t('网页上选的') }, color: 'purple' },
  auto: { get text() { return t('自动找到') }, color: 'default' },
  builtin: { get text() { return t('RapidOCR 自带') }, color: 'default' },
  none: { get text() { return t('没有') }, color: 'warning' },
}

/** Label of the "none" option per slot (the LLM cannot be disabled). */
const EMPTY: Partial<Record<ModelSlot['key'], string>> = {
  get mmproj() { return t('不用视觉模型') },
  get ocr_det() { return t('用 RapidOCR 自带的') },
  get ocr_rec() { return t('用 RapidOCR 自带的') },
  get ocr_dict() { return t('用 RapidOCR 自带的') },
  get table() { return t('不用（按文字位置划分格子）') },
}

/**
 * Model selection: only files already in models/ can be selected. To add a model, copy the file into models/
 * (OCR models into models/ocr/, table models into models/table/) and reload this page. Changes take effect
 * after `jacara stop` and `jacara start`.
 */
export function ModelsCard() {
  const { t } = useTranslation()
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
      void message.success(t('已保存。在终端里 jacara stop、再 jacara start 后生效'))
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const editable = can('system:manage')
  const changed = Object.keys(draft).length > 0

  const columns: TableProps<ModelSlot>['columns'] = [
    { title: '', dataIndex: 'label', width: 130 },
    {
      title: t('现在用的'),
      render: (_, s) => {
        const value = draft[s.key] ?? s.current ?? ''
        const options = [
          ...(EMPTY[s.key] !== undefined ? [{ value: '', label: EMPTY[s.key] }] : []),
          ...s.files.map((f) => ({ value: f.path, label: t('{{name}}（{{formatBytes}}）', { name: f.name, formatBytes: formatBytes(f.size) }) })),
        ]
        if (!editable || s.source === 'env') {
          return (
            <Typography.Text>
              {s.current ? s.current.split('/').pop() : (EMPTY[s.key] ?? t('没有'))}
            </Typography.Text>
          )
        }
        return (
          <Select
            style={{ width: '100%', maxWidth: 420 }}
            value={value}
            options={options}
            placeholder={s.files.length ? t('选一个文件') : t('models/ 里没有可选的文件')}
            onChange={(v: string) => setDraft((d) => ({ ...d, [s.key]: v }))}
          />
        )
      },
    },
    {
      title: t('谁定的'),
      width: 130,
      render: (_, s) => (
        <Tag color={SOURCE[s.source].color} title={s.source === 'env' ? t('在 .env 里改 {{env}}', { env: s.env }) : undefined}>
          {SOURCE[s.source].text}
        </Tag>
      ),
    },
  ]

  return (
    <Card
      title={t('换模型')}
      loading={models.isPending}
      extra={
        editable && (
          <Button type="primary" icon={<SwapOutlined />} disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>
            {t('保存')}
          </Button>
        )
      }
    >
      <Flex vertical gap={12}>
        {models.data?.restart_needed && (
          <Alert
            type="warning"
            showIcon
            title={t('选的大模型和正在用的（{{loaded}}）不一样：在终端里 jacara stop、再 jacara start 才会换过来', { loaded: models.data.loaded })}
          />
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('只能选本机已有的文件（离线，不会下载）。最方便的是先在')}{' '}
          <Link to="/hub">{t('模型和数据集')}</Link>{' '}{t('里导入（会检查文件、记版本），导入的版本都会出现在下拉里，名字后面写着"（模型库）"。 手动放文件也行：大模型、视觉模型放 models/，OCR 模型放 models/ocr/（文件名里带 det、rec，字典是 .txt），表格模型放 models/table/。.env 里写了的优先，这里改不了。改完要 jacara stop、jacara start 才生效。')}
        </Typography.Text>
        <Table rowKey="key" size="small" pagination={false} columns={columns} dataSource={models.data?.slots ?? []} />
      </Flex>
    </Card>
  )
}
