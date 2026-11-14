import { UndoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Input, InputNumber, Select, Switch, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { getSampleSettings, setSampleSettings } from '../../api/ocrSamples'
import { getSettings, type SettingItem, updateSettings } from '../../api/settings'
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

/**
 * One setting rendered as a switch, select, number or text input by kind. Settings fixed in .env are
 * read-only.
 */
function SettingRow({
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
        placeholder={item.kind === 'path' ? t('/Users/你的用户名/Documents/待处理单据') : t('留空')}
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
      <Flex vertical gap={2} style={{ flex: 1, minWidth: 260 }}>
        <Flex gap={8} align="center" wrap>
          <Typography.Text strong>{item.label}</Typography.Text>
          <Tooltip title={item.source === 'env' ? t('在 .env 中修改 {{env}}，重启后生效', { env: item.env }) : t('.env 中的变量名：{{env}}', { env: item.env })}>
            <Tag color={SOURCE[item.source].color}>{SOURCE[item.source].text}</Tag>
          </Tooltip>
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {item.help}
        </Typography.Text>
      </Flex>
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
  const settings = useQuery({ queryKey: ['app-settings'], queryFn: getSettings })
  const samples = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['app-settings'], data)
      void message.success(t('已保存。需执行 jacara stop 和 jacara start 后，后台才会使用新设置'))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const collect = useMutation({
    mutationFn: setSampleSettings,
    onSuccess: (data) => queryClient.setQueryData(['ocr-sample-settings'], data),
    onError: (err) => void message.error(errorText(err)),
  })
  const data = settings.data
  const groups = [...new Set((data?.items ?? []).map((i) => i.group))]
  return (
    <Flex vertical gap={16}>
      {settings.isError && <Alert type="error" showIcon title={t('无法读取设置')} description={errorText(settings.error)} />}
      {data?.restart_needed && (
        <Alert
          type="warning"
          showIcon
          title={t('修改的设置尚未生效')}
          description={t('后台仅在启动时读取设置：请在终端中运行 uv run jacara stop，再运行 uv run jacara start。')}
        />
      )}
      {data && !data.can_change && <Alert type="info" showIcon title={t('仅管理员可修改这些设置')} />}
      {groups.map((group) => (
        <Card key={group} title={group} size="small">
          {data!.items
            .filter((i) => i.group === group)
            .map((item) => (
              <SettingRow
                key={`${item.key}:${String(item.value)}`}
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
