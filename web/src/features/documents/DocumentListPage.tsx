import { ReloadOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Card, Table, type TableProps, Tag } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { listDocuments, processDocument } from '../../api/documents'
import type { DocumentSummary } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatBytes, formatTime } from '../../utils/format'
import { IN_PROGRESS } from './status'
import { StatusTag } from './StatusTag'
import { UploadArea } from './UploadArea'

const PAGE_SIZE = 20
const POLL_MS = 3000 // 有文件在排队或处理时，每 3 秒刷新一次
const STUCK_MS = 60_000 // 排队超过 1 分钟还没开始：多半是 worker 没开，或者队列丢了任务

/**
 * 能不能点"重试"：失败的，或者排队太久的。
 * 后端两种都接受；worker 按状态认领，重复放进队列也不会处理两次。
 */
function canRetry(d: DocumentSummary, now: number): boolean {
  if (d.status === 'failed') {
    return true
  }
  return d.status === 'uploaded' && now - new Date(d.created_at).getTime() > STUCK_MS
}

/** 文档列表：上传、看状态、失败重试。点文件名进入审核页。 */
export function DocumentListPage() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)

  const documents = useQuery({
    queryKey: ['documents', page],
    queryFn: () => listDocuments(PAGE_SIZE, (page - 1) * PAGE_SIZE),
    placeholderData: keepPreviousData, // 翻页时先显示上一页，新数据到了再换，表格不会闪
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => IN_PROGRESS.includes(d.status)) ? POLL_MS : false,
  })

  const retry = useMutation({
    mutationFn: processDocument,
    onSuccess: (document) => void message.success(`${document.filename} 已重新排队`),
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['documents'] }),
  })

  const columns: TableProps<DocumentSummary>['columns'] = [
    {
      title: '文件名',
      dataIndex: 'filename',
      ellipsis: true,
      render: (filename: string, d) =>
        // 处理完的才有结果可看
        d.status === 'done' || d.status === 'reviewed' ? (
          <Link to={`/documents/${d.id}`}>{filename}</Link>
        ) : (
          filename
        ),
    },
        {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (_, d) => (
        <StatusTag status={d.status} error={d.status === 'failed' ? d.error : null} />
      ),
    },
    { title: '页数', dataIndex: 'page_count', width: 70, align: 'right' },
    {
      title: '待确认',
      dataIndex: 'review_count',
      width: 90,
      align: 'right',
      render: (count: number, d) =>
        d.status !== 'done' ? '–' : count > 0 ? <Tag color="gold">{count}</Tag> : 0,
    },
    {
      title: '大小',
      dataIndex: 'size_bytes',
      width: 100,
      align: 'right',
      render: formatBytes,
    },
    { title: '上传时间', dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_, d) =>
        can('document:process') &&
        canRetry(d, documents.dataUpdatedAt) && (
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={retry.isPending && retry.variables === d.id}
            onClick={() => retry.mutate(d.id)}
          >
            重试
          </Button>
        ),
    },
  ]

  return (
    <Card
      title="文档"
      extra={
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void documents.refetch()}
          loading={documents.isFetching}
        >
          刷新
        </Button>
      }
    >
      {can('document:create') && <UploadArea />}
      {documents.error && (
        <Alert
          type="error"
          showIcon
          title={
            documents.error instanceof ApiError ? documents.error.message : String(documents.error)
          }
          style={{ marginBottom: 16 }}
        />
      )}
      <Table<DocumentSummary>
        rowKey="id"
        columns={columns}
        dataSource={documents.data?.items}
        loading={documents.isPending}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: documents.data?.total ?? 0,
          showTotal: (total) => `共 ${total} 个`,
          onChange: setPage,
        }}
        locale={{ emptyText: can('document:create') ? '还没有文档，先上传一个' : '还没有文档' }}
      />
    </Card>
  )
}
