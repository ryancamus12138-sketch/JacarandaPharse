import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Flex, Form, Input, Typography } from 'antd'
import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from './useAuth'
import { useTranslation } from 'react-i18next'
import { LanguageSwitch } from '../components/LanguageSwitch'

interface LoginForm {
  username: string
  password: string
}

export function LoginPage() {
  const { t } = useTranslation()
  const { me, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Destination before login (recorded by RequireAuth); the home page otherwise.
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  if (me !== null) {
    // Already logged in, or personal mode (no login): do not show the login page.
    return <Navigate to={from} replace />
  }

  async function submit(values: LoginForm) {
    setSubmitting(true)
    setError(null)
    try {
      await login(values.username, values.password)
      void navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh', background: '#f5f3fa', position: 'relative' }}>
      <div style={{ position: 'absolute', top: 16, right: 16 }}>
        <LanguageSwitch />
      </div>
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          JacarandaParse
        </Typography.Title>
        <Typography.Paragraph type="secondary">{t('用你的账号登录')}</Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
        <Form<LoginForm> layout="vertical" onFinish={(values) => void submit(values)}>
          <Form.Item
            name="username"
            label={t('用户名')}
            rules={[{ required: true, message: t('请输入用户名') }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            label={t('密码')}
            rules={[{ required: true, message: t('请输入密码') }]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {t('登录')}
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          {t('忘了密码？请管理员给你一个重设码，然后在这里')}
          <Link to="/reset-password">{t('用重设码设新密码')}</Link>{t('（重设码不能直接当密码登录）')}
        </Typography.Paragraph>
      </Card>
    </Flex>
  )
}
