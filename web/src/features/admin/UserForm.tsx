import { Checkbox, Form, Input, Modal, Radio } from 'antd'
import type { PermissionOption, Role, UserCreate } from '../../api/types'

interface Props {
  open: boolean
  permissions: PermissionOption[]
  saving: boolean
  onCancel: () => void
  onSubmit: (data: UserCreate) => void
}

/** "新建用户"弹窗。规则（用户名格式、密码长度、重名）后端也会检查。 */
export function UserForm({ open, permissions, saving, onCancel, onSubmit }: Props) {
  const [form] = Form.useForm<UserCreate>()
  const role = Form.useWatch('role', form)

  return (
    <Modal
      title="新建用户"
      open={open}
      okText="新建"
      confirmLoading={saving}
      onOk={() => form.submit()}
      onCancel={onCancel}
      destroyOnHidden // 关掉后清空，下次打开是空白的
    >
      <Form<UserCreate>
        form={form}
        layout="vertical"
        preserve={false}
        initialValues={{ role: 'member' satisfies Role, permissions: [], display_name: '' }}
        onFinish={onSubmit}
      >
        <Form.Item
          name="username"
          label="用户名（登录用）"
          rules={[
            { required: true, message: '请输入用户名' },
            { pattern: /^[A-Za-z0-9_.-]{2,64}$/, message: '2–64 个字符，只能用英文字母、数字和 _ . -' },
          ]}
        >
          <Input autoComplete="off" placeholder="比如 xiaoli" />
        </Form.Item>
        <Form.Item name="display_name" label="显示名字">
          <Input autoComplete="off" placeholder="比如 小李（可以不填）" maxLength={100} />
        </Form.Item>
        <Form.Item
          name="password"
          label="初始密码"
          rules={[{ required: true, min: 8, message: '至少 8 个字符' }]}
          extra="告诉对方这个密码，让他登录后找你改（或者以后在这里重设）"
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item name="role" label="角色">
          <Radio.Group
            options={[
              { value: 'member', label: '使用者（按下面勾选开通权限）' },
              { value: 'admin', label: '管理员（全部权限，能管理用户）' },
            ]}
          />
        </Form.Item>
        {role === 'member' && (
          <Form.Item
            name="permissions"
            label="开通的权限"
            extra="不勾也能看文档、下载原图、导出 Excel"
          >
            <Checkbox.Group options={permissions.map((p) => ({ value: p.name, label: p.label }))} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  )
}
