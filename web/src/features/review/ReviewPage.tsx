import { Button, Card, Empty } from 'antd'
import { Link, useParams } from 'react-router'

/** 审核工作台。下两课做：左边原图和出处框，右边字段列表。 */
export function ReviewPage() {
  const { id } = useParams()
  return (
    <Card
      title="审核"
      extra={
        <Link to="/documents">
          <Button>返回列表</Button>
        </Link>
      }
    >
      <Empty description={`下一课在这里做审核工作台（文档 ${id}）`} />
    </Card>
  )
}
