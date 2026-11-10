import { ArrowLeftOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Empty, Flex, Result, Segmented, Spin, Typography } from 'antd'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { getDocument } from '../../api/documents'
import { StatusTag } from '../documents/StatusTag'
import { FieldPanel } from './FieldPanel'
import { PageViewer } from './PageViewer'

/** 审核工作台：左边原图和出处框，右边字段。这一课只看；下一课加修改、确认和完成审核。 */
export function ReviewPage() {
  const { id = '' } = useParams()
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  const document = useQuery({ queryKey: ['document', id], queryFn: () => getDocument(id) })

  const back = (
    <Link to="/documents">
      <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
    </Link>
  )

  if (document.isPending) {
    return (
      <Flex justify="center" style={{ padding: 96 }}>
        <Spin size="large" />
      </Flex>
    )
  }
  if (document.error) {
    const message =
      document.error instanceof ApiError ? document.error.message : String(document.error)
    return <Result status="warning" title="打不开这个文档" subTitle={message} extra={back} />
  }

  const doc = document.data
  if (doc.status !== 'done' && doc.status !== 'reviewed') {
    return (
      <Result
        status="info"
        title="还没有结果"
        subTitle="文档处理完（待审核）之后才能在这里看。"
        extra={back}
      />
    )
  }

  const extraction = doc.extractions.find((e) => e.page === page)
  const changePage = (next: number) => {
    setPage(next)
    setSelectedId(null)
  }

  return (
    <Card
      title={
        <Flex align="center" gap={12}>
          <Typography.Text strong ellipsis style={{ maxWidth: 360 }}>
            {doc.filename}
          </Typography.Text>
          <StatusTag status={doc.status} />
        </Flex>
      }
      extra={back}
      styles={{ body: { paddingTop: 16 } }}
    >
      {doc.page_count > 1 && (
        <Segmented
          value={page}
          onChange={(value) => changePage(Number(value))}
          options={Array.from({ length: doc.page_count }, (_, i) => ({
            label: `第 ${i + 1} 页`,
            value: i + 1,
          }))}
          style={{ marginBottom: 16 }}
        />
      )}
      <Flex gap={24} align="stretch" style={{ height: 'calc(100vh - 230px)', minHeight: 480 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageViewer
            documentId={doc.id}
            page={page}
            values={extraction?.values ?? []}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={setSelectedId}
            onHover={setHoveredId}
          />
        </div>
        <div style={{ width: 420, flexShrink: 0, overflow: 'auto' }}>
          {extraction ? (
            <FieldPanel
              extraction={extraction}
              selectedId={selectedId}
              hoveredId={hoveredId}
              onSelect={setSelectedId}
              onHover={setHoveredId}
            />
          ) : (
            <Empty description="这一页没有抽取结果" />
          )}
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            title="下一课：在这里修改、确认字段，并完成审核"
          />
        </div>
      </Flex>
    </Card>
  )
}
