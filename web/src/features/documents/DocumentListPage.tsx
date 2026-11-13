import {
  ClearOutlined,
  DeleteOutlined,
  FolderOutlined,
  MessageOutlined,
  ReloadOutlined,
  SearchOutlined,
} from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Flex,
  Input,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../../api/client'
import {
  deleteDocuments,
  listDocuments,
  listFolders,
  processDocument,
} from '../../api/documents'
import { listTemplates } from '../../api/templates'
import type { DocumentFilters, DocumentStatus, DocumentSummary } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatBytes, formatTime } from '../../utils/format'
import { ExportButton } from './ExportButton'
import { BatchReprocessButton } from './BatchReprocess'
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
    value_q: params.get('value_q') ?? undefined,
    field: params.get('field') ?? undefined,
    skipped: params.has('skipped') ? params.get('skipped') === 'true' : undefined,
    folder: params.get('folder') ?? undefined,
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
  if (f.value_q) params.set('value_q', f.value_q)
  if (f.field) params.set('field', f.field)
  if (f.skipped !== undefined) params.set('skipped', String(f.skipped))
  if (f.folder) params.set('folder', f.folder)
  return params
}

/** 文档列表：上传、筛选、看状态和进度、失败重试、勾选导出。点文件名进入审核页。 */
export function DocumentListPage() {
  const { can } = useAuth()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  // 勾选了哪些文档（要导出的）。翻页时保留，可以跨页勾选
  const [selected, setSelected] = useState<string[]>([])
  // 勾选的那几行（翻页后也记着）：状态会变，重新处理前要看有没有正在处理的
  const [pickedRows, setPickedRows] = useState<Record<string, DocumentSummary>>({})
  const [params, setParams] = useSearchParams()
  const filters = filtersFrom(params)
  const filtered = params.toString() !== ''
  // 搜索框打字时先不查，按回车或点搜索再查
  const [search, setSearch] = useState(filters.q ?? '')
  const [valueSearch, setValueSearch] = useState(filters.value_q ?? '')
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, staleTime: Infinity })
  const folders = useQuery({ queryKey: ['folders'], queryFn: listFolders })

  // "在哪个字段里找"的选项：选了模板就是这个模板的字段，没选就是所有模板的字段（同一个 ID 只列一次）
  const fieldOptions = (() => {
    const seen = new Map<string, string>()
    for (const t of templates.data ?? []) {
      if (filters.template && t.name !== filters.template) continue
      for (const f of t.fields) if (!seen.has(f.name)) seen.set(f.name, f.label || f.name)
    }
    return [...seen].map(([value, label]) => ({ value, label }))
  })()

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

  const remove = useMutation({
    mutationFn: deleteDocuments,
    onSuccess: ({ deleted, skipped }, ids) => {
      void message.success(`删除了 ${deleted} 份`)
      for (const reason of skipped) void message.warning(`没删：${reason}`)
      setSelected((s) => s.filter((id) => !ids.includes(id)))
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['folders'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  const canDelete = can('document:delete')

  /** 勾选的这一份现在是什么状态：当前页上有就用最新的，翻走了就用勾选时记下的。 */
  function statusOf(id: string): DocumentStatus | undefined {
    return documents.data?.items.find((d) => d.id === id)?.status ?? pickedRows[id]?.status
  }
  const busyPicked = selected.filter((id) => IN_PROGRESS.includes(statusOf(id) as DocumentStatus))

  /** 选中的里面有正在处理、排队中的：不重新处理，提示取消勾选。 */
  function warnBusy() {
    const names = busyPicked.map(
      (id) => documents.data?.items.find((d) => d.id === id)?.filename ?? pickedRows[id]?.filename ?? id,
    )
    modal.confirm({
      title: `有 ${names.length} 份正在处理，不能重新处理`,
      content: `${names.slice(0, 5).join('、')}${names.length > 5 ? ` 等 ${names.length} 份` : ''}。请取消勾选后再试。`,
      okText: '取消勾选这些',
      cancelText: '返回',
      onOk: () => setSelected((s) => s.filter((id) => !busyPicked.includes(id))),
    })
  }


  const columns: TableProps<DocumentSummary>['columns'] = [
    {
      title: '文件名',
      dataIndex: 'filename',
      ellipsis: true,
      render: (filename: string, d) => (
        <Flex gap={6} align="center" style={{ minWidth: 0 }}>
          {/* 处理完的才有结果可看 */}
          <Typography.Text ellipsis style={{ minWidth: 0 }}>
            {d.status === 'done' || d.status === 'reviewed' ? (
              <Link to={`/documents/${d.id}`}>{filename}</Link>
            ) : (
              filename
            )}
          </Typography.Text>
          {d.skipped_at && (
            <Tooltip title={d.skip_reason ? `跳过原因：${d.skip_reason}` : '审核时先跳过了'}>
              <Tag color="orange" style={{ marginInlineEnd: 0 }}>
                已跳过
              </Tag>
            </Tooltip>
          )}
          {(d.same_file ?? []).length > 0 && (
            <Tooltip
              title={`内容完全一样的文件以前传过：${(d.same_file ?? [])
                .map((o) => `${o.filename}（${formatTime(o.created_at)}）`)
                .join('、')}`}
            >
              <Tag color="red" style={{ marginInlineEnd: 0 }}>
                重复的文件
              </Tag>
            </Tooltip>
          )}
          {d.folder && (
            <Tooltip title="批量导入时所在的文件夹，点一下只看这个文件夹">
              <Tag
                icon={<FolderOutlined />}
                style={{ marginInlineEnd: 0, cursor: 'pointer' }}
                onClick={() => setFilters({ folder: d.folder ?? undefined })}
              >
                {d.folder}
              </Tag>
            </Tooltip>
          )}
          {(d.open_comments ?? 0) > 0 && (
            <Tooltip title={`${d.open_comments} 条备注没解决`}>
              <Tag icon={<MessageOutlined />} style={{ marginInlineEnd: 0 }}>
                {d.open_comments}
              </Tag>
            </Tooltip>
          )}
        </Flex>
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
      width: 170,
      render: (_, d) => (
        <Flex gap={4}>
          {can('document:process') && canRetry(d, documents.dataUpdatedAt) && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={retry.isPending && retry.variables === d.id}
              onClick={() => retry.mutate(d.id)}
            >
              重试
            </Button>
          )}
          {canDelete && d.status !== 'processing' && (
            <Popconfirm
              title={`删除「${d.filename}」？`}
              description="原始文件、处理结果、审核记录、备注都会删掉，不能恢复。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => remove.mutate([d.id])}
            >
              <Button size="small" danger type="text" icon={<DeleteOutlined />} aria-label="删除">
                删除
              </Button>
            </Popconfirm>
          )}
        </Flex>
      ),
    },
  ]

  return (
    <Card
      title="文档"
      extra={
        <Flex gap={8}>
          {can('document:process') && (
            <BatchReprocessButton
              ids={selected}
              busy={busyPicked.length > 0}
              onBusy={warnBusy}
              onDone={(ids) => {
                setSelected((s) => s.filter((id) => !ids.includes(id)))
                void queryClient.invalidateQueries({ queryKey: ['documents'] })
                void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
              }}
            />
          )}
          {canDelete && (
            <Popconfirm
              title={`删除选中的 ${selected.length} 份？`}
              description="原始文件、处理结果、审核记录、备注都会删掉，不能恢复。正在处理的会跳过。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              disabled={selected.length === 0}
              onConfirm={() => remove.mutate(selected)}
            >
              <Tooltip title={selected.length === 0 ? '先在左边勾选要删除的' : undefined}>
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={selected.length === 0}
                  loading={remove.isPending}
                >
                  删除{selected.length > 0 ? `（${selected.length}）` : ''}
                </Button>
              </Tooltip>
            </Popconfirm>
          )}
          <ExportButton ids={selected.filter((id) => statusOf(id) !== 'failed')} type="primary" />
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
        {/* 选字段 + 输入内容放在一起，换行时也不分开 */}
        <Space.Compact>
          <Select<string>
            allowClear
            showSearch={{ optionFilterProp: 'label' }}
            placeholder="所有字段"
            aria-label="在哪个字段里找"
            value={filters.field}
            onChange={(field) => setFilters({ field })}
            options={fieldOptions}
            style={{ width: 140 }}
            popupMatchSelectWidth={false}
          />
          <Input.Search
            allowClear
            placeholder="字段内容，如发票号码、商家"
            aria-label="按字段内容找"
            value={valueSearch}
            onChange={(e) => setValueSearch(e.target.value)}
            onSearch={(v) => setFilters({ value_q: v.trim() || undefined })}
            enterButton={<SearchOutlined />}
            style={{ width: 240 }}
          />
        </Space.Compact>
        {(folders.data ?? []).length > 0 && (
          <Select<string>
            allowClear
            showSearch
            placeholder="全部文件夹"
            aria-label="文件夹"
            value={filters.folder}
            onChange={(folder) => setFilters({ folder })}
            options={(folders.data ?? []).map((f) => ({
              value: f.folder,
              label: `${f.folder}（${f.count}）`,
            }))}
            style={{ minWidth: 180 }}
            popupMatchSelectWidth={false}
          />
        )}
        <Select<string>
          aria-label="跳过的文档"
          value={filters.skipped === undefined ? 'all' : filters.skipped ? 'only' : 'hide'}
          onChange={(v) => setFilters({ skipped: v === 'all' ? undefined : v === 'only' })}
          options={[
            { value: 'all', label: '跳过的也显示' },
            { value: 'only', label: '只看跳过的' },
            { value: 'hide', label: '不看跳过的' },
          ]}
          style={{ width: 130 }}
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
              setValueSearch('')
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
          onChange: (keys, rows) => {
            setSelected(keys.map(String))
            setPickedRows((old) => {
              const next: Record<string, DocumentSummary> = {}
              for (const key of keys.map(String)) {
                const row = rows.find((r) => r?.id === key) ?? old[key]
                if (row) next[key] = row
              }
              return next
            })
          },
          preserveSelectedRowKeys: true, // 翻到别的页，已经勾的不会丢
          // 排队中、处理中的不能勾：还没有结果（不能导出），也不能重新处理、删除；失败的可以重新处理、删除
          // 勾选以后才开始处理的，还能取消勾选
          getCheckboxProps: (d) => ({
            disabled: !EXPORTABLE.includes(d.status) && d.status !== 'failed' && !selected.includes(d.id),
          }),
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
