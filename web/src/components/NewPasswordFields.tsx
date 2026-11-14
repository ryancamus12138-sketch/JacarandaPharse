import { Form, Input } from 'antd'
import { useTranslation } from 'react-i18next'

/**
 * "New password + confirm" input pair shared by several forms. At least 8 characters; both entries must
 * match.
 */
export function NewPasswordFields() {
  const { t } = useTranslation()
  return (
    <>
      <Form.Item
        name="password"
        label={t('新密码')}
        rules={[{ required: true, min: 8, message: t('至少 8 个字符') }]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      <Form.Item
        name="again"
        label={t('再输入一次')}
        dependencies={['password']}
        rules={[
          { required: true, message: t('请再输入一次') },
          ({ getFieldValue }) => ({
            validator: (_, value) =>
              value === getFieldValue('password')
                ? Promise.resolve()
                : Promise.reject(new Error(t('两次输入的不一样'))),
          }),
        ]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
    </>
  )
}
