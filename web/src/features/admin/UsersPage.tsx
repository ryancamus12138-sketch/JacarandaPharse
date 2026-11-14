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
import { useDesktop } from '../../desktop'
import { formatTime } from '../../utils/format'
import { ResetCodeModal } from './ResetCodeModal'
import { UserForm } from './UserForm'
import { useTranslation } from 'react-i18next'
import { msg } from '../../i18n'

const SELF_LOCKED = msg('不能修改自己的角色或停用自己，以免无人可管理系统')

/**
 * User management (administrators only): create users, grant permissions with checkboxes, change roles,
 * deactivate, generate reset codes.
 */
export function UsersPage() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const desktop = useDesktop()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [issued, setIssued] = useState<ResetCode | null>(null)

  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, enabled: me?.mode === 'team' })
  const permissions = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    enabled: me?.mode === 'team',
    staleTime: Infinity, // Grantable permissions are fixed in the backend and never change.
  })

  const showError = (err: Error) =>
    void message.error(err instanceof ApiError ? err.message : String(err))

  const update = useMutation({
    mutationFn: ({ id, change }: { id: string; change: UserUpdate }) => updateUser(id, change),
    onSuccess: (user) => void message.success(t('已更新 {{value}}', { value: user.display_name || user.username })),
    onError: showError,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const create = useMutation({
    mutationFn: (data: UserCreate) => createUser(data),
    onSuccess: (user) => {
      void message.success(t('已新建 {{username}}', { username: user.username }))
      setCreating(false)
    },
    onError: showError,
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  const resetCode = useMutation({
    mutationFn: (user: User) => createResetCode(user.id),
    onSuccess: setIssued, // Show the code in a dialog.
    onError: showError,
  })

  if (me?.mode !== 'team') {
    return (
      <Result
        status="info"
        title={t('个人模式无需账号')}
        subTitle={
          desktop
            ? t('个人版只供本机一人使用，没有账号和登录。')
            : t('多人使用时，请将 .env 中的 JACARA_MODE 改为 team，重启后端，并使用 jacara user add 创建第一个管理员。')
        }
      />
    )
  }

  const options = permissions.data ?? []
  const columns: TableProps<User>['columns'] = [
    {
      title: t('用户'),
      dataIndex: 'username',
      render: (username: string, u) => (
        <>
          <Typography.Text strong>{u.display_name || username}</Typography.Text>
          {u.display_name && <Typography.Text type="secondary"> {username}</Typography.Text>}
          {u.id === me.user?.id && <Tag style={{ marginInlineStart: 8 }}>{t('你')}</Tag>}
        </>
      ),
    },
    {
      title: t('角色'),
      dataIndex: 'role',
      width: 130,
      render: (role: Role, u) => (
        <Tooltip title={u.id === me.user?.id ? t(SELF_LOCKED) : undefined}>
          <Select<Role>
            value={role}
            size="small"
            style={{ width: 100 }}
            disabled={u.id === me.user?.id}
            options={[
              { value: 'member', label: t('使用者') },
              { value: 'admin', label: t('管理员') },
            ]}
            onChange={(value) => update.mutate({ id: u.id, change: { role: value } })}
          />
        </Tooltip>
      ),
    },
    {
      title: t('开通的权限'),
      dataIndex: 'permissions',
      render: (granted: string[], u) =>
        u.role === 'admin' ? (
          <Typography.Text type="secondary">{t('管理员有全部权限')}</Typography.Text>
        ) : (
          // Each checkbox change is sent immediately and takes effect without the user logging in again.
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
      title: t('启用'),
      dataIndex: 'is_active',
      width: 80,
      render: (active: boolean, u) => (
        <Tooltip title={u.id === me.user?.id ? t(SELF_LOCKED) : active ? t('停用后无法登录') : undefined}>
          <Switch
            checked={active}
            size="small"
            disabled={u.id === me.user?.id}
            onChange={(checked) => update.mutate({ id: u.id, change: { is_active: checked } })}
          />
        </Tooltip>
      ),
    },
    { title: t('创建时间'), dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: t('操作'),
      key: 'actions',
      width: 100,
      render: (_, u) => (
        <Tooltip title={t('用户忘记密码时使用：生成一次性重设码，由用户自行设置新密码')}>
          <Button
            size="small"
            icon={<KeyOutlined />}
            disabled={!u.is_active}
            loading={resetCode.isPending && resetCode.variables?.id === u.id}
            onClick={() => resetCode.mutate(u)}
          >
            {t('重设码')}
          </Button>
        </Tooltip>
      ),
    },
  ]

  return (
    <Card
      title={t('用户管理')}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          {t('新建用户')}
        </Button>
      }
    >
      <Typography.Paragraph type="secondary">
        {t('使用者默认可查看文档、下载原图和导出 Excel；其他权限在此勾选开通，修改即时生效。用户不可删除，只能停用（修改记录和审计日志需要保留操作人信息）。')}
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
