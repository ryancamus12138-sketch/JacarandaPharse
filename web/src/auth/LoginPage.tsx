import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Flex, Form, Input, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import { useAuth } from './useAuth'

interface LoginForm {
  username: string
  password: string
}

export function LoginPage() {
  const { me, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // 登录前想去的地址（RequireAuth 记下的），没有就去首页
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  if (me !== null) {
    // 已经登录了，或者是个人模式（不用登录）：不显示登录页
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
    <Flex justify="center" align="center" style={{ minHeight: '100vh', background: '#f5f3fa' }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          JacarandaParse
        </Typography.Title>
        <Typography.Paragraph type="secondary">用你的账号登录</Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
        <Form<LoginForm> layout="vertical" onFinish={(values) => void submit(values)}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          忘了密码请找管理员重设。
        </Typography.Paragraph>
      </Card>
    </Flex>
  )
}
