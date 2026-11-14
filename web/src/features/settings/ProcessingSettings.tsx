import { DeleteOutlined, FolderOpenOutlined, PlusOutlined, UndoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Input, InputNumber, Select, Space, Switch, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { getSampleSettings, setSampleSettings } from '../../api/ocrSamples'
import { chooseFolder } from '../../api/desktop'
import { getSettings, type SettingItem, updateSettings, type WatchFolder } from '../../api/settings'
import { listTemplates } from '../../api/templates'
import { useDesktop } from '../../desktop'
import { AUTO, DISCOVER, settingTemplateOptions } from '../templates/uploadChoice'
import { RestartServicesButton } from './RestartServicesButton'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

const SOURCE = {
  env: { get text() { return t('由 .env 指定') }, color: 'gold' },
  page: { get text() { return t('已修改') }, color: 'blue' },
  default: { get text() { return t('默认') }, color: 'default' },
} as const

/** Example of a full folder path in the notation of this computer's system. */
function folderExample(): string {
  return /Windows/i.test(navigator.userAgent)
    ? t('C:\\Users\\你的用户名\\Documents\\待处理单据')
    : t('/Users/你的用户名/Documents/待处理单据')
}

/**
 * A template setting: the same choices as at upload. A template that has since been archived or deleted is
 * shown as an error so that it can be chosen again.
 */
function TemplateSetting({
  value,
  disabled,
  onChange,
  width = 360,
}: {
  value: string
  disabled: boolean
  onChange: (value: unknown) => void
  width?: number
}) {
  const { t } = useTranslation()
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const mine = (templates.data ?? []).filter((tpl) => tpl.kind === 'mine' && !tpl.archived)
  const known = value === AUTO || value === DISCOVER || mine.some((tpl) => tpl.id === value)
  const missing = templates.isSuccess && !known
  return (
    <Select
      style={{ width }}
      loading={templates.isPending}
      value={known ? value : undefined}
      status={missing ? 'error' : undefined}
      placeholder={missing ? t('所选模板已归档或删除，请重新选择') : undefined}
      disabled={disabled}
      onChange={onChange}
      options={settingTemplateOptions(templates.data ?? [])}
      showSearch={{ optionFilterProp: 'search' }}
    />
  )
}

/** Name, where the value comes from, and the explanation of one setting. */
function SettingHeader({ item }: { item: SettingItem }) {
  const { t } = useTranslation()
  const desktop = useDesktop()
  return (
    <Flex vertical gap={2} style={{ flex: 1, minWidth: 260 }}>
      <Flex gap={8} align="center" wrap>
        <Typography.Text strong>{item.label}</Typography.Text>
        {desktop ? (
          // The desktop app has no .env; a value fixed outside the page comes from how it was started.
          <Tag color={SOURCE[item.source].color}>
            {item.source === 'env' ? t('由启动参数指定') : SOURCE[item.source].text}
          </Tag>
        ) : (
          <Tooltip title={item.source === 'env' ? t('在 .env 中修改 {{env}}，重启后生效', { env: item.env }) : t('.env 中的变量名：{{env}}', { env: item.env })}>
            <Tag color={SOURCE[item.source].color}>{SOURCE[item.source].text}</Tag>
          </Tooltip>
        )}
      </Flex>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {item.help}
      </Typography.Text>
    </Flex>
  )
}

/**
 * Watch folders: several folders, each with the template its files are processed with. Edited as a list and
 * saved together; in the desktop app a folder is chosen with the system dialog.
 */
function WatchFoldersRow({
  item,
  disabled,
  onChange,
}: {
  item: SettingItem
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const desktop = useDesktop()
  const saved = Array.isArray(item.value) ? item.value : []
  const [rows, setRows] = useState<WatchFolder[]>(saved)
  const locked = disabled || item.source === 'env'
  const changed = JSON.stringify(rows) !== JSON.stringify(saved)
  const complete = rows.every((r) => r.path.trim() !== '')
  const update = (index: number, patch: Partial<WatchFolder>) =>
    setRows(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  const ask = async (): Promise<string | null> => {
    try {
      return await chooseFolder('watch')
    } catch (err) {
      void message.error(errorText(err))
      return null
    }
  }
  const add = async () => {
    if (desktop) {
      try {
        const path = await chooseFolder('watch')
        if (path) {
          setRows([...rows, { path, template: AUTO }])
        }
        return
      } catch (err) {
        // No system dialog on this computer: add a row to type the path in.
        void message.error(errorText(err))
      }
    }
    setRows([...rows, { path: '', template: AUTO }])
  }
  return (
    <Flex vertical gap={12} style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
      <Flex justify="space-between" align="center" gap={16} wrap>
        <SettingHeader item={item} />
        {item.source === 'page' && !disabled && (
          <Tooltip title={t('恢复默认')}>
            <Button size="small" type="text" icon={<UndoOutlined />} onClick={() => onChange(null)} />
          </Tooltip>
        )}
      </Flex>
      {rows.length === 0 && <Typography.Text type="secondary">{t('未设置监视文件夹')}</Typography.Text>}
      {rows.map((row, index) => (
        <Flex key={index} gap={8} align="center" wrap>
          <Space.Compact style={{ flex: 1, minWidth: 320 }}>
            <Input
              value={row.path}
              disabled={locked}
              placeholder={folderExample()}
              onChange={(e) => update(index, { path: e.target.value })}
            />
            {desktop && (
              <Button
                icon={<FolderOpenOutlined />}
                disabled={locked}
                onClick={() =>
                  void ask().then((path) => {
                    if (path) update(index, { path })
                  })
                }
              >
                {t('选择…')}
              </Button>
            )}
          </Space.Compact>
          <TemplateSetting width={300} value={row.template} disabled={locked} onChange={(v) => update(index, { template: String(v) })} />
          <Tooltip title={t('不再监视这个文件夹（文件夹和其中的文件不受影响）')}>
            <Button type="text" danger icon={<DeleteOutlined />} disabled={locked} onClick={() => setRows(rows.filter((_, i) => i !== index))} />
          </Tooltip>
        </Flex>
      ))}
      {!locked && (
        <Flex gap={8}>
          <Button icon={<PlusOutlined />} onClick={() => void add()}>
            {t('添加文件夹')}
          </Button>
          <Button type="primary" disabled={!changed || !complete} onClick={() => onChange(rows.map((r) => ({ path: r.path.trim(), template: r.template })))}>
            {t('保存')}
          </Button>
          {changed && <Button onClick={() => setRows(saved)}>{t('撤销')}</Button>}
        </Flex>
      )}
    </Flex>
  )
}

/**
 * One setting rendered as a switch, select, number or text input by kind. Settings fixed in .env are
 * read-only.
 */
export function SettingRow({
  item,
  disabled,
  onChange,
}: {
  item: SettingItem
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(item.value === null ? '' : String(item.value))
  const locked = disabled || item.source === 'env'
  if (item.kind === 'watch_folders') {
    return <WatchFoldersRow item={item} disabled={disabled} onChange={onChange} />
  }
  let control
  if (item.kind === 'bool') {
    control = <Switch checked={Boolean(item.value)} disabled={locked} onChange={onChange} />
  } else if (item.kind === 'choice') {
    control = (
      <Select
        style={{ width: 260 }}
        value={item.value as string}
        disabled={locked}
        onChange={onChange}
        options={item.options}
      />
    )
  } else if (item.kind === 'template') {
    control = <TemplateSetting value={item.value as string} disabled={locked} onChange={onChange} />
  } else if (item.kind === 'int') {
    control = (
      <InputNumber
        style={{ width: 160 }}
        value={item.value as number}
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        disabled={locked}
        onBlur={(e) => {
          const n = Number(e.target.value)
          if (Number.isFinite(n) && n !== item.value) onChange(n)
        }}
      />
    )
  } else {
    control = (
      <Input
        style={{ width: 360 }}
        value={draft}
        disabled={locked}
        placeholder={item.kind === 'path' ? folderExample() : t('留空')}
        onChange={(e) => setDraft(e.target.value)}
        onPressEnter={() => onChange(draft.trim() || null)}
        onBlur={() => {
          if ((draft.trim() || null) !== item.value) onChange(draft.trim() || null)
        }}
      />
    )
  }
  return (
    <Flex justify="space-between" align="center" gap={16} wrap style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
      <SettingHeader item={item} />
      <Flex gap={8} align="center">
        {control}
        {item.source === 'page' && !disabled && (
          <Tooltip title={t('恢复默认')}>
            <Button size="small" type="text" icon={<UndoOutlined />} onClick={() => onChange(null)} />
          </Tooltip>
        )}
      </Flex>
    </Flex>
  )
}

/**
 * Settings → Recognition and processing: switches formerly available only in .env are editable here; values
 * in .env take precedence.
 */
export function ProcessingSettings() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const desktop = useDesktop()
  const settings = useQuery({ queryKey: ['app-settings'], queryFn: getSettings })
  const samples = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data, values) => {
      queryClient.setQueryData(['app-settings'], data)
      const live = Object.keys(values).every((key) => data.items.find((i) => i.key === key)?.live)
      void message.success(
        live
          ? t('已保存，立即生效')
          : desktop
            ? t('已保存。点击“重新启动后台”后生效')
            : t('已保存。需执行 jacara stop 和 jacara start 后，后台才会使用新设置'),
      )
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const collect = useMutation({
    mutationFn: setSampleSettings,
    onSuccess: (data) => queryClient.setQueryData(['ocr-sample-settings'], data),
    onError: (err) => void message.error(errorText(err)),
  })
  const data = settings.data
  // Settings of the data page (automatic backups) are shown there.
  const shown = (data?.items ?? []).filter((i) => i.section === 'processing')
  const groups = [...new Set(shown.map((i) => i.group))]
  return (
    <Flex vertical gap={16}>
      {settings.isError && <Alert type="error" showIcon title={t('无法读取设置')} description={errorText(settings.error)} />}
      {data?.restart_needed && (
        <Alert
          type="warning"
          showIcon
          title={t('修改的设置尚未生效')}
          description={
            desktop
              ? t('后台仅在启动时读取设置。点击“重新启动后台”即可应用，正在处理的文档会在重启后继续。')
              : t('后台仅在启动时读取设置：请在终端中运行 uv run jacara stop，再运行 uv run jacara start。')
          }
          action={desktop && data.can_change ? <RestartServicesButton /> : undefined}
        />
      )}
      {data && !data.can_change && <Alert type="info" showIcon title={t('仅管理员可修改这些设置')} />}
      {groups.map((group) => (
        <Card key={group} title={group} size="small">
          {shown
            .filter((i) => i.group === group)
            .map((item) => (
              <SettingRow
                key={`${item.key}:${JSON.stringify(item.value)}`}
                item={item}
                disabled={!data!.can_change || save.isPending}
                onChange={(value) => save.mutate({ [item.key]: value })}
              />
            ))}
          {/* The OCR sample switch belongs to the group of the learning-from-corrections settings. */}
          {group === data!.items.find((i) => i.key === 'jacara_learn')?.group && samples.data && (
            <Flex justify="space-between" align="center" gap={16} wrap style={{ padding: '12px 0' }}>
              <Flex vertical gap={2} style={{ flex: 1, minWidth: 260 }}>
                <Typography.Text strong>{t('审核时自动收集 OCR 纠错样本')}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  {t('在审核页点击原文时先核对“图中原文”，保存审核时一并保存为 OCR 训练样本。与“OCR 样本”页顶部的开关为同一设置。')}
                </Typography.Text>
              </Flex>
              <Switch
                checked={samples.data.collect}
                disabled={!samples.data.can_change}
                loading={collect.isPending}
                onChange={(v) => collect.mutate(v)}
              />
            </Flex>
          )}
        </Card>
      ))}
    </Flex>
  )
}
