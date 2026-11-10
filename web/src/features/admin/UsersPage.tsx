import { Card, Empty } from 'antd'

/** 用户管理（只有管理员能看）。以后做：新建用户、勾选开通权限、停用。 */
export function UsersPage() {
  return (
    <Card title="用户管理">
      <Empty description="以后在这里管理用户和权限" />
    </Card>
  )
}
