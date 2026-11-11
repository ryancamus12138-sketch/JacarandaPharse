import { useMutation } from '@tanstack/react-query'
import { App, Form, Input, Modal, Typography } from 'antd'
import { changePassword } from '../api/auth'
import { ApiError } from '../api/client'
import { NewPasswordFields } from './NewPasswordFields'

interface ChangePasswordForm {
  old: string
  password: string
  again: string
}

/** 右上角"修改密码"弹窗：原密码 + 新密码两遍。 */
export function ChangePassword({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const [form] = Form.useForm<ChangePasswordForm>()
  const save = useMutation({
    mutationFn: (v: ChangePasswordForm) => changePassword(v.old, v.password),
    onSuccess: () => {
      void message.success('密码已修改。别的电脑、浏览器上的登录都已退出')
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <Modal
      title="修改密码"
      open={open}
      okText="修改"
      confirmLoading={save.isPending}
      onOk={() => form.submit()}
      onCancel={onClose}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">
        改完以后，你在别的电脑、浏览器上的登录都会退出；这里不会。
      </Typography.Paragraph>
      <Form<ChangePasswordForm>
        form={form}
        layout="vertical"
        preserve={false}
        onFinish={(v) => save.mutate(v)}
      >
        <Form.Item name="old" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <NewPasswordFields />
      </Form>
    </Modal>
  )
}
