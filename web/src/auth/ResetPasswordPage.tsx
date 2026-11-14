import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Form, Input, Typography } from 'antd'
import { Link, useNavigate } from 'react-router'
import { resetWithCode } from '../api/auth'
import { ApiError } from '../api/client'
import { NewPasswordFields } from '../components/NewPasswordFields'
import { useTranslation } from 'react-i18next'
import { LanguageSwitch } from '../components/LanguageSwitch'

interface ResetForm {
  username: string
  code: string
  password: string
  again: string
}

/**
 * Forgotten password: enter the reset code from an administrator and set a new password. Accessible without
 * login.
 */
export function ResetPasswordPage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const navigate = useNavigate()
  const reset = useMutation({
    mutationFn: (v: ResetForm) => resetWithCode(v.username, v.code, v.password),
    onSuccess: () => {
      void message.success(t('新密码已设好，请用新密码登录'))
      void navigate('/login', { replace: true })
    },
  })

  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh', background: '#f5f3fa', position: 'relative' }}>
      <div style={{ position: 'absolute', top: 16, right: 16 }}>
        <LanguageSwitch />
      </div>
      <Card style={{ width: 400 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          {t('忘记密码')}
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          {t('请管理员在"用户管理"里给你生成一个')}<strong>{t('重设码')}</strong>{t('（15 分钟有效，只能用一次），然后在这里自己设新密码。管理员不会知道你的新密码。')}
        </Typography.Paragraph>
        {reset.error && (
          <Alert
            type="error"
            showIcon
            title={reset.error instanceof ApiError ? reset.error.message : String(reset.error)}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form<ResetForm> layout="vertical" onFinish={(v) => reset.mutate(v)}>
          <Form.Item
            name="username"
            label={t('用户名')}
            rules={[{ required: true, message: t('请输入用户名') }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item
            name="code"
            label={t('重设码')}
            rules={[{ required: true, message: t('请输入管理员给你的重设码') }]}
          >
            <Input
              prefix={<LockOutlined />}
              placeholder={t('比如 K7QM-3XWD（不分大小写）')}
              autoComplete="one-time-code"
            />
          </Form.Item>
          <NewPasswordFields />
          <Button type="primary" htmlType="submit" block loading={reset.isPending}>
            {t('设新密码')}
          </Button>
        </Form>
        <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          <Link to="/login">{t('返回登录')}</Link>
        </Typography.Paragraph>
      </Card>
    </Flex>
  )
}
