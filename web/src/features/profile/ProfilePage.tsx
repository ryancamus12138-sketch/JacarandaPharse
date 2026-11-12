import { DeleteOutlined, KeyOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Flex,
  Form,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { useState } from 'react'
import { getProfile, removeAvatar, updateProfile, uploadAvatar } from '../../api/auth'
import { ApiError } from '../../api/client'
import type { Profile } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { ChangePassword } from '../../components/ChangePassword'
import { UserAvatar } from '../../components/UserAvatar'

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err))

/** 个人资料：头像、昵称、账号信息、修改密码。右上角的名字和左边的头像都点到这里。 */
export function ProfilePage() {
  const { message } = App.useApp()
  const { refresh } = useAuth()
  const client = useQueryClient()
  const profile = useQuery({ queryKey: ['profile'], queryFn: getProfile })
  // 正在改的昵称；null = 还没动过，显示保存着的那个
  const [edited, setEdited] = useState<string | null>(null)
  const [changingPassword, setChangingPassword] = useState(false)

  /** 改完以后：页面上的资料、右上角的名字和头像都跟着变。 */
  async function changed(next: Profile) {
    client.setQueryData(['profile'], next)
    await refresh()
  }

  const saveName = useMutation({
    mutationFn: (name: string) => updateProfile(name),
    onSuccess: async (next) => {
      await changed(next)
      setEdited(null)
      void message.success('昵称已保存')
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const upload = useMutation({
    mutationFn: uploadAvatar,
    onSuccess: async (next) => {
      await changed(next)
      void message.success('头像已更换')
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: removeAvatar,
    onSuccess: async (next) => {
      await changed(next)
      void message.success('头像已去掉')
    },
    onError: (err) => void message.error(errorText(err)),
  })

  if (profile.isPending) return <Spin />
  if (profile.isError) return <Alert type="error" showIcon title={errorText(profile.error)} />
  const p = profile.data
  const team = p.mode === 'team'
  const name = edited ?? p.display_name

  return (
    <Flex vertical gap={16} style={{ maxWidth: 720 }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        个人资料
      </Typography.Title>

      <Card title="头像和昵称">
        <Flex gap={24} wrap align="flex-start">
          <Flex vertical align="center" gap={8}>
            <UserAvatar size={96} />
            <Space>
              <Upload
                accept="image/png,image/jpeg,image/webp"
                showUploadList={false}
                beforeUpload={(file) => {
                  upload.mutate(file)
                  return false // 不让 Upload 自己发请求，用上面的 upload
                }}
              >
                <Button size="small" icon={<UploadOutlined />} loading={upload.isPending}>
                  换头像
                </Button>
              </Upload>
              {p.avatar_at && (
                <Button
                  size="small"
                  icon={<DeleteOutlined />}
                  loading={remove.isPending}
                  onClick={() => remove.mutate()}
                >
                  去掉
                </Button>
              )}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              PNG、JPG、WEBP，5 MB 以内
            </Typography.Text>
          </Flex>
          <Form layout="vertical" style={{ flex: 1, minWidth: 240 }} onFinish={() => saveName.mutate(name)}>
            <Form.Item
              label="昵称"
              extra={
                team
                  ? `别人在备注、修改记录里看到的名字。不填就显示用户名 ${p.username ?? ''}`
                  : '个人模式下右上角显示的名字。不填就显示"本机用户"'
              }
            >
              <Input
                value={name}
                maxLength={100}
                placeholder={team ? (p.username ?? '') : '本机用户'}
                onChange={(e) => setEdited(e.target.value)}
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={saveName.isPending}
              disabled={name.trim() === p.display_name}
            >
              保存
            </Button>
          </Form>
        </Flex>
      </Card>

      <Card
        title="账号"
        extra={
          team && (
            <Button icon={<KeyOutlined />} onClick={() => setChangingPassword(true)}>
              修改密码
            </Button>
          )
        }
      >
        {team ? (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="用户名">{p.username}</Descriptions.Item>
            <Descriptions.Item label="团队">{p.team || '—'}</Descriptions.Item>
            <Descriptions.Item label="角色">
              <Tag color={p.role === 'admin' ? 'purple' : 'default'}>
                {p.role === 'admin' ? '管理员' : '使用者'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="能做的事">
              {p.role === 'admin' ? (
                '全部（包括管理用户、看审计日志）'
              ) : (
                <Space size={[4, 4]} wrap>
                  <Tag>看、下载、导出</Tag>
                  {p.permissions.map((name) => (
                    <Tag key={name}>{name}</Tag>
                  ))}
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="账号建于">
              {p.member_since ? new Date(p.member_since).toLocaleDateString() : '—'}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Typography.Text type="secondary">
            个人模式：这台电脑上不用登录，也没有用户名和密码。要多人使用，请让管理员切到团队模式。
          </Typography.Text>
        )}
      </Card>
      <ChangePassword open={changingPassword} onClose={() => setChangingPassword(false)} />
    </Flex>
  )
}
