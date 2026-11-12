import { ClearOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Flex,
  Input,
  Progress,
  Select,
  Table,
  type TableProps,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../../api/client'
import { listDocuments, processDocument } from '../../api/documents'
import { listTemplates } from '../../api/templates'
import type { DocumentFilters, DocumentStatus, DocumentSummary } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatBytes, formatTime } from '../../utils/format'
import { ExportButton } from './ExportButton'
import { EXPORTABLE, IN_PROGRESS } from './status'
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

const STATUS_OPTIONS: { value: DocumentStatus; label: string }[] = [
  { value: 'uploaded', label: '排队中' },
  { value: 'processing', label: '处理中' },
  { value: 'done', label: '待审核' },
  { value: 'failed', label: '失败' },
  { value: 'reviewed', label: '已审核' },
]

/** 筛选条件放在网址里（?status=done&q=costco）：刷新、后退、从工作台点进来都能保持。 */
function filtersFrom(params: URLSearchParams): DocumentFilters {
  return {
    status: params.getAll('status') as DocumentStatus[],
    template: params.get('template') ?? undefined,
    date_from: params.get('date_from') ?? undefined,
    date_to: params.get('date_to') ?? undefined,
    q: params.get('q') ?? undefined,
    pending: params.get('pending') === 'true',
  }
}

function paramsFrom(f: DocumentFilters): URLSearchParams {
  const params = new URLSearchParams()
  for (const status of f.status ?? []) params.append('status', status)
  if (f.template) params.set('template', f.template)
  if (f.date_from) params.set('date_from', f.date_from)
  if (f.date_to) params.set('date_to', f.date_to)
  if (f.q) params.set('q', f.q)
  if (f.pending) params.set('pending', 'true')
  return params
}

/** 文档列表：上传、筛选、看状态和进度、失败重试、勾选导出。点文件名进入审核页。 */
export function DocumentListPage() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  // 勾选了哪些文档（要导出的）。翻页时保留，可以跨页勾选
  const [selected, setSelected] = useState<string[]>([])
  const [params, setParams] = useSearchParams()
  const filters = filtersFrom(params)
  const filtered = params.toString() !== ''
  // 搜索框打字时先不查，按回车或点搜索再查
  const [search, setSearch] = useState(filters.q ?? '')
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, staleTime: Infinity })

  function setFilters(change: Partial<DocumentFilters>) {
    setParams(paramsFrom({ ...filters, ...change }), { replace: true })
    setPage(1) // 条件变了，从第一页看起
  }

  const documents = useQuery({
    queryKey: ['documents', page, params.toString()],
    queryFn: () => listDocuments(PAGE_SIZE, (page - 1) * PAGE_SIZE, filters),
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
      width: 150,
      render: (_, d) =>
        d.status === 'processing' && d.progress !== null ? (
          // 处理中：显示做到哪一步
          <Flex vertical gap={0}>
            <Progress percent={d.progress} size="small" status="active" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {d.stage}
            </Typography.Text>
          </Flex>
        ) : (
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
        <Flex gap={8}>
          <ExportButton ids={selected} type="primary" />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void documents.refetch()}
            loading={documents.isFetching}
          >
            刷新
          </Button>
        </Flex>
      }
    >
      {can('document:create') && <UploadArea />}
      <Flex gap={8} wrap align="center" style={{ marginBottom: 12 }}>
        <Select<DocumentStatus[]>
          mode="multiple"
          allowClear
          placeholder="全部状态"
          value={filters.status}
          onChange={(status) => setFilters({ status })}
          options={STATUS_OPTIONS}
          style={{ minWidth: 180 }}
          maxTagCount="responsive"
        />
        <Select<string>
          allowClear
          placeholder="全部模板"
          value={filters.template}
          onChange={(template) => setFilters({ template })}
          options={templates.data?.map((t) => ({
            value: t.name,
            label: t.kind === 'builtin' ? `${t.title}（内置）` : t.title,
          }))}
          style={{ minWidth: 160 }}
          popupMatchSelectWidth={false}
        />
        <Flex gap={4} align="center">
          <Input
            type="date"
            aria-label="上传日期从"
            value={filters.date_from ?? ''}
            onChange={(e) => setFilters({ date_from: e.target.value || undefined })}
            style={{ width: 150 }}
          />
          <Typography.Text type="secondary">至</Typography.Text>
          <Input
            type="date"
            aria-label="上传日期到"
            value={filters.date_to ?? ''}
            onChange={(e) => setFilters({ date_to: e.target.value || undefined })}
            style={{ width: 150 }}
          />
        </Flex>
        <Input.Search
          allowClear
          placeholder="文件名"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(q) => setFilters({ q: q.trim() || undefined })}
          enterButton={<SearchOutlined />}
          style={{ width: 200 }}
        />
        <Checkbox
          checked={filters.pending}
          onChange={(e) => setFilters({ pending: e.target.checked })}
        >
          只看有待确认字段的
        </Checkbox>
        {filtered && (
          <Button
            type="link"
            icon={<ClearOutlined />}
            onClick={() => {
              setSearch('')
              setParams(new URLSearchParams(), { replace: true })
              setPage(1)
            }}
          >
            清除筛选
          </Button>
        )}
      </Flex>
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
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys.map(String)),
          preserveSelectedRowKeys: true, // 翻到别的页，已经勾的不会丢
          // 还没处理完的没有结果，不能导出
          getCheckboxProps: (d) => ({ disabled: !EXPORTABLE.includes(d.status) }),
        }}
        dataSource={documents.data?.items}
        loading={documents.isPending}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: documents.data?.total ?? 0,
          showTotal: (total) => `共 ${total} 个`,
          onChange: setPage,
        }}
        locale={{
          emptyText: filtered
            ? '没有符合条件的文档'
            : can('document:create')
              ? '还没有文档，先上传一个'
              : '还没有文档',
        }}
      />
    </Card>
  )
}
