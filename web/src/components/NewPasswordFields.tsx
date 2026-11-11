import { Form, Input } from 'antd'

/** "新密码 + 再输入一次"两个输入框，几个地方共用。至少 8 个字符，两次要一样。 */
export function NewPasswordFields() {
  return (
    <>
      <Form.Item
        name="password"
        label="新密码"
        rules={[{ required: true, min: 8, message: '至少 8 个字符' }]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
      <Form.Item
        name="again"
        label="再输入一次"
        dependencies={['password']}
        rules={[
          { required: true, message: '请再输入一次' },
          ({ getFieldValue }) => ({
            validator: (_, value) =>
              value === getFieldValue('password')
                ? Promise.resolve()
                : Promise.reject(new Error('两次输入的不一样')),
          }),
        ]}
      >
        <Input.Password autoComplete="new-password" />
      </Form.Item>
    </>
  )
}
