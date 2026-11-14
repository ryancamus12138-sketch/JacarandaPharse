import { SwapOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Select, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { chooseModels, getModels, type ModelSlot } from '../../api/models'
import { useAuth } from '../../auth/useAuth'
import { useDesktop } from '../../desktop'
import { formatBytes } from '../../utils/format'
import { RestartServicesButton } from './RestartServicesButton'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const SOURCE: Record<ModelSlot['source'], { text: string; color: string }> = {
  env: { get text() { return t('.env 指定') }, color: 'blue' },
  choice: { get text() { return t('手动选择') }, color: 'purple' },
  auto: { get text() { return t('自动检测') }, color: 'default' },
  builtin: { get text() { return t('默认') }, color: 'default' },
  bundled: { get text() { return t('默认') }, color: 'default' },
  none: { get text() { return t('无') }, color: 'warning' },
}

/** Label of the "none" option per slot (the LLM cannot be disabled). */
const EMPTY: Partial<Record<ModelSlot['key'], string>> = {
  get mmproj() { return t('不使用视觉模型') },
  get table() { return t('不使用（按文字位置划分单元格）') },
}

/** The "nothing chosen" choice: the slot's default (OCR: the model shipped in RapidOCR) or switching it off. */
function emptyLabel(s: ModelSlot): string | undefined {
  return s.default_label ?? EMPTY[s.key]
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
  const desktop = useDesktop()
  const queryClient = useQueryClient()
  const models = useQuery({ queryKey: ['models'], queryFn: getModels })
  const [draft, setDraft] = useState<Partial<Record<ModelSlot['key'], string>>>({})
  const save = useMutation({
    mutationFn: () => chooseModels(draft),
    onSuccess: (data) => {
      queryClient.setQueryData(['models'], data)
      setDraft({})
      void message.success(desktop ? t('已保存。点击“重新启动后台”后生效') : t('已保存。在终端中执行 jacara stop 和 jacara start 后生效'))
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const editable = can('system:manage')
  const changed = Object.keys(draft).length > 0

  const columns: TableProps<ModelSlot>['columns'] = [
    { title: '', dataIndex: 'label', width: 130 },
    {
      title: t('当前使用'),
      render: (_, s) => {
        const value = draft[s.key] ?? s.current ?? ''
        const options = [
          ...(emptyLabel(s) !== undefined ? [{ value: '', label: emptyLabel(s) }] : []),
          ...s.files.map((f) => ({
            value: f.path,
            label: f.provider
              ? t('{{name}}（{{provider}}，{{size}}）', { name: f.name, provider: f.provider, size: formatBytes(f.size) })
              : t('{{name}}（{{formatBytes}}）', { name: f.name, formatBytes: formatBytes(f.size) }),
          })),
        ]
        if (!editable || s.source === 'env') {
          return (
            <Typography.Text>
              {s.current ? s.current.split('/').pop() : (emptyLabel(s) ?? t('无'))}
            </Typography.Text>
          )
        }
        return (
          <Select
            style={{ width: '100%', maxWidth: 420 }}
            value={value}
            options={options}
            placeholder={s.files.length ? t('选择文件') : t('models/ 中没有可选文件')}
            onChange={(v: string) => setDraft((d) => ({ ...d, [s.key]: v }))}
          />
        )
      },
    },
    {
      title: t('来源'),
      width: 130,
      render: (_, s) => (
        <Tag
          color={SOURCE[s.source].color}
          title={s.source === 'env' && !desktop ? t('在 .env 中修改 {{env}}', { env: s.env }) : undefined}
        >
          {s.source === 'env' && desktop ? t('由启动参数指定') : SOURCE[s.source].text}
        </Tag>
      ),
    },
  ]

  return (
    <Card
      title={t('更换模型')}
      loading={models.isPending}
      extra={
        editable && (
          <Flex gap={8} align="center">
            {/* Desktop app: models are loaded at start; this applies saved choices now. */}
            {desktop && <RestartServicesButton inAlert={false} />}
            <Button type="primary" icon={<SwapOutlined />} disabled={!changed} loading={save.isPending} onClick={() => save.mutate()}>
              {t('保存')}
            </Button>
          </Flex>
        )
      }
    >
      <Flex vertical gap={12}>
        {models.data?.restart_needed && (
          <Alert
            type="warning"
            showIcon
            title={
              desktop
                ? t('所选大模型与当前运行的模型（{{loaded}}）不一致：点击“重新启动后台”后切换', { loaded: models.data.loaded })
                : t('所选大模型与当前运行的模型（{{loaded}}）不一致：需在终端中执行 jacara stop 和 jacara start 后才会切换', { loaded: models.data.loaded })
            }
          />
        )}
        {desktop && (
          <Typography.Text>
            {t('还没有推荐的大模型和视觉模型，或想重新下载？')} <Link to="/setup">{t('下载推荐模型')}</Link>
          </Typography.Text>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {desktop ? (
            <>
              {t('下拉列表中是默认模型和已导入的模型。导入新模型：')}
              <Link to="/setup">{t('准备模型')}</Link>
              {t('（下载、放入文件夹或选择文件）或')}{' '}
              <Link to="/hub">{t('模型和数据集')}</Link>
              {t('。修改后点击“重新启动后台”生效。')}
            </>
          ) : (
            <>
              {t('仅可选择本机已有的模型文件，不会联网下载。推荐通过')}{' '}
              <Link to="/hub">{t('模型和数据集')}</Link>{' '}
              {t('导入（导入时校验文件并记录版本），导入的版本会出现在下拉列表中。也可手动放置文件：大模型和视觉模型放在 models/，OCR 模型放在 models/ocr/（文件名需包含 det 或 rec，字典为 .txt），表格模型放在 models/table/。.env 中已指定的项优先生效，无法在此修改。修改后需执行 jacara stop 和 jacara start 才会生效。')}
            </>
          )}
        </Typography.Text>
        <Table rowKey="key" size="small" pagination={false} columns={columns} dataSource={models.data?.slots ?? []} />
      </Flex>
    </Card>
  )
}
