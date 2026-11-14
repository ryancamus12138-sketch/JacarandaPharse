import { DownloadOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Input, Select, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { type AuditFilters, exportAuditLogs, listAuditLogs } from '../../api/audit'
import { ApiError } from '../../api/client'
import type { AuditLog } from '../../api/types'
import { listPermissions, listUsers } from '../../api/users'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'
import { ACTION_FILTERS, ACTION_LABELS, describe } from './auditLabels'
import { AuditTarget } from './AuditTarget'
import { useTranslation } from 'react-i18next'

const PAGE_SIZE = 50

/**
 * Audit log (administrators only): who did what, and when. Read-only; entries cannot be changed or deleted.
 */
export function AuditLogPage() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const { message } = App.useApp()
  const [page, setPage] = useState(1)
  const [filters, setFiltersState] = useState<AuditFilters>({})
  const setFilters = (change: AuditFilters) => {
    setFiltersState((f) => ({ ...f, ...change }))
    setPage(1)
  }

  const logs = useQuery({
    queryKey: ['audit-logs', page, filters],
    queryFn: () => listAuditLogs(PAGE_SIZE, (page - 1) * PAGE_SIZE, filters),
    placeholderData: keepPreviousData,
  })
  // Entries carry only user_id; map it to names through the user list (personal mode has no user list).
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, enabled: me?.mode === 'team' })
  const permissions = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    enabled: me?.mode === 'team',
    staleTime: Infinity,
  })
  const permission = (name: string) =>
    permissions.data?.find((p) => p.name === name)?.label ?? name
  const userName = (id: string) => {
    const user = users.data?.find((u) => u.id === id)
    return user ? user.display_name || user.username : me?.mode === 'personal' ? t('你') : t('（未知）')
  }

  const userLabel = (id: string | null) => (id ? userName(id) : t('（用户名不存在）'))

  // Export CSV: all entries matching the current filters, rendered as on the page (the export itself is also
  // logged).
  const download = useMutation({
    mutationFn: () => exportAuditLogs(filters),
    onSuccess: ({ items, total }) => {
      const cell = (text: string) => `"${text.replaceAll('"', '""')}"`
      const rows = [
        [t('时间'), t('操作人'), t('操作'), t('详情'), t('对象类型'), t('对象'), t('原始内容')],
        ...items.map((log) => [
          formatTime(log.created_at),
          userLabel(log.user_id),
          ACTION_LABELS[log.action] ?? log.action,
          describe(log, permission),
          log.target_type ?? '',
          log.target_id ?? '',
          JSON.stringify(log.detail),
        ]),
      ]
      const csv = '\ufeff' + rows.map((r) => r.map((c) => cell(String(c))).join(',')).join('\r\n')
      const day = new Date().toISOString().slice(0, 10)
      void saveFile({ blob: new Blob([csv], { type: 'text/csv;charset=utf-8' }), filename: t('审计日志-{{day}}.csv', { day }) })
      if (total > items.length) void message.warning(t('共 {{total}} 条，仅导出了最新的 {{length}} 条，请缩小筛选范围', { total, length: items.length }))
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const columns: TableProps<AuditLog>['columns'] = [
    { title: t('时间'), dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: t('操作人'),
      dataIndex: 'user_id',
      width: 120,
      render: (id: string | null) =>
        id ? userName(id) : <Typography.Text type="secondary">{t('（用户名不存在）')}</Typography.Text>,
    },
    {
      title: t('操作'),
      dataIndex: 'action',
      width: 140,
      render: (value: string) => (
        // Failed logins and wrong reset codes are red, so administrators spot repeated attempts at a glance.
        <Tag color={value.endsWith('_failed') ? 'red' : undefined} style={{ whiteSpace: 'normal', marginInlineEnd: 0 }}>
          {ACTION_LABELS[value] ?? value}
        </Tag>
      ),
    },
    { title: t('详情'), key: 'detail', ellipsis: true, render: (_, log) => describe(log, permission) },
    {
      title: t('对象'),
      key: 'target',
      width: 110,
      render: (_, log) => <AuditTarget log={log} userName={userName} />,
    },
  ]

  return (
    <Card
      title={t('审计日志')}
      extra={
        <Button icon={<DownloadOutlined />} loading={download.isPending} onClick={() => download.mutate()}>
          {t('导出 CSV')}
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        {t('记录登录、上传、审核、导出、用户管理及密码修改等操作。记录只读，不可修改或删除；导出操作本身也会记录。')}
      </Typography.Paragraph>
      <Flex gap={8} wrap style={{ marginBottom: 16 }}>
        <Select
          value={filters.action ?? ''}
          style={{ width: 240 }}
          options={ACTION_FILTERS}
          onChange={(value) => setFilters({ action: value || undefined })}
        />
        {me?.mode === 'team' && (
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder={t('所有人')}
            style={{ width: 180 }}
            value={filters.user_id}
            onChange={(value) => setFilters({ user_id: value || undefined })}
            options={(users.data ?? []).map((u) => ({
              value: u.id,
              label: u.display_name ? t('{{display_name}}（{{username}}）', { display_name: u.display_name, username: u.username }) : u.username,
            }))}
          />
        )}
        <Flex gap={4} align="center">
          <Input
            type="date"
            aria-label={t('开始日期')}
            value={filters.date_from ?? ''}
            onChange={(e) => setFilters({ date_from: e.target.value || undefined })}
            style={{ width: 150 }}
          />
          <Typography.Text type="secondary">{t('至')}</Typography.Text>
          <Input
            type="date"
            aria-label={t('结束日期')}
            value={filters.date_to ?? ''}
            onChange={(e) => setFilters({ date_to: e.target.value || undefined })}
            style={{ width: 150 }}
          />
        </Flex>
      </Flex>
      {logs.error && (
        <Alert type="error" showIcon title={logs.error.message} style={{ marginBottom: 16 }} />
      )}
      <Table<AuditLog>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={logs.data?.items}
        loading={logs.isPending}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: logs.data?.total ?? 0,
          showTotal: (total) => t('共 {{total}} 条', { total }),
          showSizeChanger: false,
          onChange: setPage,
        }}
      />
    </Card>
  )
}
