import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Alert, Card, Select, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { listAuditLogs } from '../../api/audit'
import type { AuditLog } from '../../api/types'
import { listPermissions, listUsers } from '../../api/users'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { ACTION_FILTERS, ACTION_LABELS, describe } from './auditLabels'
import { AuditTarget } from './AuditTarget'

const PAGE_SIZE = 50

/** 审计日志（只有管理员能看）：谁、什么时候、做了什么。只能看，不能改也不能删。 */
export function AuditLogPage() {
  const { me } = useAuth()
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')

  const logs = useQuery({
    queryKey: ['audit-logs', page, action],
    queryFn: () => listAuditLogs(PAGE_SIZE, (page - 1) * PAGE_SIZE, action || undefined),
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
        <Tag color={value.endsWith('_failed') ? 'red' : undefined}>
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
        <Select
          value={action}
          style={{ width: 240 }}
          options={ACTION_FILTERS}
          onChange={(value) => {
            setAction(value)
            setPage(1)
          }}
        />
      }
    >
      <Typography.Paragraph type="secondary">
        登录、上传、审核、导出、用户管理、改密码都会记在这里。记录只能看，不能改也不能删。
      </Typography.Paragraph>
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
