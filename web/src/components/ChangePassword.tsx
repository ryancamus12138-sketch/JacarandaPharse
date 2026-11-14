import { useMutation } from '@tanstack/react-query'
import { App, Form, Input, Modal, Typography } from 'antd'
import { changePassword } from '../api/auth'
import { ApiError } from '../api/client'
import { NewPasswordFields } from './NewPasswordFields'
import { useTranslation } from 'react-i18next'

interface ChangePasswordForm {
  old: string
  password: string
  again: string
}

/** "Change password" dialog from the top-right corner: current password + new password twice. */
export function ChangePassword({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [form] = Form.useForm<ChangePasswordForm>()
  const save = useMutation({
    mutationFn: (v: ChangePasswordForm) => changePassword(v.old, v.password),
    onSuccess: () => {
      void message.success(t('密码已修改。别的电脑、浏览器上的登录都已退出'))
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <Modal
      title={t('修改密码')}
      open={open}
      okText={t('修改')}
      confirmLoading={save.isPending}
      onOk={() => form.submit()}
      onCancel={onClose}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">
        {t('改完以后，你在别的电脑、浏览器上的登录都会退出；这里不会。')}
      </Typography.Paragraph>
      <Form<ChangePasswordForm>
        form={form}
        layout="vertical"
        preserve={false}
        onFinish={(v) => save.mutate(v)}
      >
        <Form.Item name="old" label={t('原密码')} rules={[{ required: true, message: t('请输入原密码') }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <NewPasswordFields />
      </Form>
    </Modal>
  )
}
