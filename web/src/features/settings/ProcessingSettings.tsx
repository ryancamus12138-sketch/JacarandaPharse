import { UndoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Input, InputNumber, Select, Switch, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { getSampleSettings, setSampleSettings } from '../../api/ocrSamples'
import { getSettings, type SettingItem, updateSettings } from '../../api/settings'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

const SOURCE = {
  env: { text: '由 .env 指定', color: 'gold' },
  page: { text: '改过', color: 'blue' },
  default: { text: '默认', color: 'default' },
} as const

/** 一项设置：按种类给开关、下拉、数字或文本框。.env 写了的只能看。 */
function SettingRow({
  item,
  disabled,
  onChange,
}: {
  item: SettingItem
  disabled: boolean
  onChange: (value: unknown) => void
}) {
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
        placeholder={item.kind === 'path' ? '/Users/你的用户名/Documents/待处理单据' : '空着'}
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
          <Tooltip title={item.source === 'env' ? `在 .env 里改 ${item.env}，重启后生效` : `.env 里的名字：${item.env}`}>
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
          <Tooltip title="改回默认">
            <Button size="small" type="text" icon={<UndoOutlined />} onClick={() => onChange(null)} />
          </Tooltip>
        )}
      </Flex>
    </Flex>
  )
}

/** 设置 → 识别和处理（ADR 0063）：原来只能写 .env 的开关，页面上能改；.env 写了的优先。 */
export function ProcessingSettings() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['app-settings'], queryFn: getSettings })
  const samples = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['app-settings'], data)
      void message.success('保存了。后台要 jacara stop、jacara start 才用新的设置')
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
      {settings.isError && <Alert type="error" showIcon title="设置读不出来" description={errorText(settings.error)} />}
      {data?.restart_needed && (
        <Alert
          type="warning"
          showIcon
          title="改过的设置还没生效"
          description="后台启动时才读设置：在终端里运行 uv run jacara stop，再 uv run jacara start。"
        />
      )}
      {data && !data.can_change && <Alert type="info" showIcon title="只有管理员能改这些设置" />}
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
          {group === '从人改过的地方学' && samples.data && (
            <Flex justify="space-between" align="center" gap={16} wrap style={{ padding: '12px 0' }}>
              <Flex vertical gap={2} style={{ flex: 1, minWidth: 260 }}>
                <Typography.Text strong>审核时自动收集 OCR 纠错样本</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  审核页点原文时先核对"图中原文"，保存审核的同时存成训练 OCR 的样本（ADR 0050）。和"OCR 样本"页顶上的开关是同一个。
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
