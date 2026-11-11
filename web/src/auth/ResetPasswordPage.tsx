import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, App, Button, Card, Flex, Form, Input, Typography } from 'antd'
import { Link, useNavigate } from 'react-router'
import { resetWithCode } from '../api/auth'
import { ApiError } from '../api/client'
import { NewPasswordFields } from '../components/NewPasswordFields'

interface ResetForm {
  username: string
  code: string
  password: string
  again: string
}

/** 忘记密码：输入管理员给的重设码，自己设新密码。不用登录就能打开。 */
export function ResetPasswordPage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const reset = useMutation({
    mutationFn: (v: ResetForm) => resetWithCode(v.username, v.code, v.password),
    onSuccess: () => {
      void message.success('新密码已设好，请用新密码登录')
      void navigate('/login', { replace: true })
    },
  })

  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh', background: '#f5f3fa' }}>
      <Card style={{ width: 400 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          忘记密码
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          请管理员在"用户管理"里给你生成一个<strong>重设码</strong>（15
          分钟有效，只能用一次），然后在这里自己设新密码。管理员不会知道你的新密码。
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
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item
            name="code"
            label="重设码"
            rules={[{ required: true, message: '请输入管理员给你的重设码' }]}
          >
            <Input
              prefix={<LockOutlined />}
              placeholder="比如 K7QM-3XWD（不分大小写）"
              autoComplete="one-time-code"
            />
          </Form.Item>
          <NewPasswordFields />
          <Button type="primary" htmlType="submit" block loading={reset.isPending}>
            设新密码
          </Button>
        </Form>
        <Typography.Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          <Link to="/login">返回登录</Link>
        </Typography.Paragraph>
      </Card>
    </Flex>
  )
}
