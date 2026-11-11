import { KeyOutlined, PlusOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Result,
  Select,
  Switch,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import type { ResetCode, Role, User, UserCreate, UserUpdate } from '../../api/types'
import { createResetCode, createUser, listPermissions, listUsers, updateUser } from '../../api/users'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { ResetCodeModal } from './ResetCodeModal'
import { UserForm } from './UserForm'

const SELF_LOCKED = '不能改自己的角色或停用自己，免得没人能管理'

/** 用户管理（只有管理员能看）：新建用户、勾选开通权限、改角色、停用、生成重设码。 */
export function UsersPage() {
  const { me } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [issued, setIssued] = useState<ResetCode | null>(null)

  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, enabled: me?.mode === 'team' })
  const permissions = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    enabled: me?.mode === 'team',
    staleTime: Infinity, // 可开通的权限是写死在后端的，不会变
  })

  const showError = (err: Error) =>
    void message.error(err instanceof ApiError ? err.message : String(err))

  const update = useMutation({
    mutationFn: ({ id, change }: { id: string; change: UserUpdate }) => updateUser(id, change),
    onSuccess: (user) => void message.success(`已更新 ${user.display_name || user.username}`),
    onError: showError,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const create = useMutation({
    mutationFn: (data: UserCreate) => createUser(data),
    onSuccess: (user) => {
      void message.success(`已新建 ${user.username}`)
      setCreating(false)
    },
    onError: showError,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const resetCode = useMutation({
    mutationFn: (user: User) => createResetCode(user.id),
    onSuccess: setIssued, // 弹窗显示这个码
    onError: showError,
  })

  if (me?.mode !== 'team') {
    return (
      <Result
        status="info"
        title="个人模式不用账号"
        subTitle="几个人一起用时，把 .env 里的 JACARA_MODE 改成 team，重启后端，用 jacara user add 建第一个管理员。"
      />
    )
  }

  const options = permissions.data ?? []
  const columns: TableProps<User>['columns'] = [
    {
      title: '用户',
      dataIndex: 'username',
      render: (username: string, u) => (
        <>
          <Typography.Text strong>{u.display_name || username}</Typography.Text>
          {u.display_name && <Typography.Text type="secondary"> {username}</Typography.Text>}
          {u.id === me.user?.id && <Tag style={{ marginInlineStart: 8 }}>你</Tag>}
        </>
      ),
    },
    {
      title: '角色',
      dataIndex: 'role',
      width: 130,
      render: (role: Role, u) => (
        <Tooltip title={u.id === me.user?.id ? SELF_LOCKED : undefined}>
          <Select<Role>
            value={role}
            size="small"
            style={{ width: 100 }}
            disabled={u.id === me.user?.id}
            options={[
              { value: 'member', label: '使用者' },
              { value: 'admin', label: '管理员' },
            ]}
            onChange={(value) => update.mutate({ id: u.id, change: { role: value } })}
          />
        </Tooltip>
      ),
    },
    {
      title: '开通的权限',
      dataIndex: 'permissions',
      render: (granted: string[], u) =>
        u.role === 'admin' ? (
          <Typography.Text type="secondary">管理员有全部权限</Typography.Text>
        ) : (
          // 勾一下就发给后端，马上生效，对方不用重新登录
          <Checkbox.Group
            value={granted}
            options={options.map((p) => ({ value: p.name, label: p.label }))}
            onChange={(next) =>
              update.mutate({ id: u.id, change: { permissions: next.map(String) } })
            }
          />
        ),
    },
    {
      title: '启用',
      dataIndex: 'is_active',
      width: 80,
      render: (active: boolean, u) => (
        <Tooltip title={u.id === me.user?.id ? SELF_LOCKED : active ? '停用后不能登录' : undefined}>
          <Switch
            checked={active}
            size="small"
            disabled={u.id === me.user?.id}
            onChange={(checked) => update.mutate({ id: u.id, change: { is_active: checked } })}
          />
        </Tooltip>
      ),
    },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_, u) => (
        <Tooltip title="对方忘了密码时用：生成一次性重设码，对方自己设新密码">
          <Button
            size="small"
            icon={<KeyOutlined />}
            disabled={!u.is_active}
            loading={resetCode.isPending && resetCode.variables?.id === u.id}
            onClick={() => resetCode.mutate(u)}
          >
            重设码
          </Button>
        </Tooltip>
      ),
    },
  ]

  return (
    <Card
      title="用户管理"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          新建用户
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        使用者默认能看文档、下载原图、导出 Excel；其他权限在这里勾选开通，改了马上生效。
        用户不能删除，只能停用（修改记录和审计日志里还要知道"是谁"）。
      </Typography.Paragraph>
      {users.error && (
        <Alert type="error" showIcon title={users.error.message} style={{ marginBottom: 16 }} />
      )}
      <Table<User>
        rowKey="id"
        columns={columns}
        dataSource={users.data}
        loading={users.isPending}
        pagination={false}
        onRow={(u) => ({ style: u.is_active ? undefined : { opacity: 0.5 } })}
      />
      <UserForm
        open={creating}
        permissions={options}
        saving={create.isPending}
        onCancel={() => setCreating(false)}
        onSubmit={(data) => create.mutate(data)}
      />
      <ResetCodeModal code={issued} onClose={() => setIssued(null)} />
    </Card>
  )
}
