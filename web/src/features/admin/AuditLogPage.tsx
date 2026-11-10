import { Card, Empty } from 'antd'

/** 审计日志（只有管理员能看）。以后做：谁、什么时候、做了什么。 */
export function AuditLogPage() {
  return (
    <Card title="审计日志">
      <Empty description="以后在这里看审计日志" />
    </Card>
  )
}
