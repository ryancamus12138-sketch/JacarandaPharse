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

const PAGE_SIZE = 50

/** 审计日志（只有管理员能看）：谁、什么时候、做了什么。只能看，不能改也不能删。 */
export function AuditLogPage() {
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
  // 日志里只有 user_id，用用户列表把它换成名字（个人模式没有用户列表）
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
    return user ? user.display_name || user.username : me?.mode === 'personal' ? '你' : '（未知）'
  }

  const userLabel = (id: string | null) => (id ? userName(id) : '（不存在的用户名）')

  // 导出 CSV：按现在的筛选条件取出全部，写成和页面上一样的人话（导出本身也记一条审计日志）
  const download = useMutation({
    mutationFn: () => exportAuditLogs(filters),
    onSuccess: ({ items, total }) => {
      const cell = (text: string) => `"${text.replaceAll('"', '""')}"`
      const rows = [
        ['时间', '谁', '操作', '详情', '对象类型', '对象', '原始内容'],
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
      saveFile(new Blob([csv], { type: 'text/csv;charset=utf-8' }), `审计日志-${day}.csv`)
      if (total > items.length) void message.warning(`一共 ${total} 条，只导出了最新的 ${items.length} 条，请缩小范围`)
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const columns: TableProps<AuditLog>['columns'] = [
    { title: '时间', dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: '谁',
      dataIndex: 'user_id',
      width: 120,
      render: (id: string | null) =>
        id ? userName(id) : <Typography.Text type="secondary">（不存在的用户名）</Typography.Text>,
    },
    {
      title: '操作',
      dataIndex: 'action',
      width: 140,
      render: (value: string) => (
        // 登录失败、重设码不对：标红，管理员一眼能看到"有人在试"
        <Tag color={value.endsWith('_failed') ? 'red' : undefined} style={{ whiteSpace: 'normal', marginInlineEnd: 0 }}>
          {ACTION_LABELS[value] ?? value}
        </Tag>
      ),
    },
    { title: '详情', key: 'detail', ellipsis: true, render: (_, log) => describe(log, permission) },
    {
      title: '对象',
      key: 'target',
      width: 110,
      render: (_, log) => <AuditTarget log={log} userName={userName} />,
    },
  ]

  return (
    <Card
      title="审计日志"
      extra={
        <Button icon={<DownloadOutlined />} loading={download.isPending} onClick={() => download.mutate()}>
          导出 CSV
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        登录、上传、审核、导出、用户管理、改密码都会记在这里。记录只能看，不能改也不能删；导出也会记一条。
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
            placeholder="所有人"
            style={{ width: 180 }}
            value={filters.user_id}
            onChange={(value) => setFilters({ user_id: value || undefined })}
            options={(users.data ?? []).map((u) => ({
              value: u.id,
              label: u.display_name ? `${u.display_name}（${u.username}）` : u.username,
            }))}
          />
        )}
        <Flex gap={4} align="center">
          <Input
            type="date"
            aria-label="从哪天"
            value={filters.date_from ?? ''}
            onChange={(e) => setFilters({ date_from: e.target.value || undefined })}
            style={{ width: 150 }}
          />
          <Typography.Text type="secondary">至</Typography.Text>
          <Input
            type="date"
            aria-label="到哪天"
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
          showTotal: (total) => `共 ${total} 条`,
          showSizeChanger: false,
          onChange: setPage,
        }}
      />
    </Card>
  )
}
