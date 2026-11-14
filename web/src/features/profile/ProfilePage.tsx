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
import { useTranslation } from 'react-i18next'

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err))

/**
 * Profile: avatar, display name, account information, password change. Reached from the name in the top-right
 * corner and the avatar in the sidebar.
 */
export function ProfilePage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const { refresh } = useAuth()
  const client = useQueryClient()
  const profile = useQuery({ queryKey: ['profile'], queryFn: getProfile })
  // Display name being edited; null = untouched, showing the saved one.
  const [edited, setEdited] = useState<string | null>(null)
  const [changingPassword, setChangingPassword] = useState(false)

  /** After changes, the profile, the name in the top-right corner and the avatar all update. */
  async function changed(next: Profile) {
    client.setQueryData(['profile'], next)
    await refresh()
  }

  const saveName = useMutation({
    mutationFn: (name: string) => updateProfile(name),
    onSuccess: async (next) => {
      await changed(next)
      setEdited(null)
      void message.success(t('昵称已保存'))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const upload = useMutation({
    mutationFn: uploadAvatar,
    onSuccess: async (next) => {
      await changed(next)
      void message.success(t('头像已更换'))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: removeAvatar,
    onSuccess: async (next) => {
      await changed(next)
      void message.success(t('头像已移除'))
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
        {t('个人资料')}
      </Typography.Title>

      <Card title={t('头像和昵称')}>
        <Flex gap={24} wrap align="flex-start">
          <Flex vertical align="center" gap={8}>
            <UserAvatar size={96} />
            <Space>
              <Upload
                accept="image/png,image/jpeg,image/webp"
                showUploadList={false}
                beforeUpload={(file) => {
                  upload.mutate(file)
                  return false // Upload must not send requests itself; upload above handles it.
                }}
              >
                <Button size="small" icon={<UploadOutlined />} loading={upload.isPending}>
                  {t('更换头像')}
                </Button>
              </Upload>
              {p.avatar_at && (
                <Button
                  size="small"
                  icon={<DeleteOutlined />}
                  loading={remove.isPending}
                  onClick={() => remove.mutate()}
                >
                  {t('移除')}
                </Button>
              )}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('PNG、JPG、WEBP，5 MB 以内')}
            </Typography.Text>
          </Flex>
          <Form layout="vertical" style={{ flex: 1, minWidth: 240 }} onFinish={() => saveName.mutate(name)}>
            <Form.Item
              label={t('昵称')}
              extra={
                team
                  ? t('在备注和修改记录中向他人显示的名称；未填写时显示用户名 {{value}}', { value: p.username ?? '' })
                  : t('个人模式下右上角显示的名称；未填写时显示“本机用户”')
              }
            >
              <Input
                value={name}
                maxLength={100}
                placeholder={team ? (p.username ?? '') : t('本机用户')}
                onChange={(e) => setEdited(e.target.value)}
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={saveName.isPending}
              disabled={name.trim() === p.display_name}
            >
              {t('保存')}
            </Button>
          </Form>
        </Flex>
      </Card>

      <Card
        title={t('账号')}
        extra={
          team && (
            <Button icon={<KeyOutlined />} onClick={() => setChangingPassword(true)}>
              {t('修改密码')}
            </Button>
          )
        }
      >
        {team ? (
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t('用户名')}>{p.username}</Descriptions.Item>
            <Descriptions.Item label={t('团队')}>{p.team || '—'}</Descriptions.Item>
            <Descriptions.Item label={t('角色')}>
              <Tag color={p.role === 'admin' ? 'purple' : 'default'}>
                {p.role === 'admin' ? t('管理员') : t('使用者')}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label={t('权限')}>
              {p.role === 'admin' ? (
                t('全部（包括用户管理和查看审计日志）')
              ) : (
                <Space size={[4, 4]} wrap>
                  <Tag>{t('查看、下载、导出')}</Tag>
                  {p.permissions.map((name) => (
                    <Tag key={name}>{name}</Tag>
                  ))}
                </Space>
              )}
            </Descriptions.Item>
            <Descriptions.Item label={t('账号创建时间')}>
              {p.member_since ? new Date(p.member_since).toLocaleDateString() : '—'}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Typography.Text type="secondary">
            {t('个人模式：在本机使用无需登录，也没有用户名和密码。如需多人使用，请由管理员切换至团队模式。')}
          </Typography.Text>
        )}
      </Card>
      <ChangePassword open={changingPassword} onClose={() => setChangingPassword(false)} />
    </Flex>
  )
}
