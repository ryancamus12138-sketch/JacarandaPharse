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
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const PAGE_SIZE = 20
const POLL_MS = 3000 // Refresh every 3 seconds while files are queued or processing.
const STUCK_MS = 60_000 // Queued for over a minute without starting: usually no worker is running or the queue lost the task.

/**
 * Whether "Retry" is available: failed documents, or documents queued too long.
 * The backend accepts both; workers claim by status, so requeueing never processes a document twice.
 */
function canRetry(d: DocumentSummary, now: number): boolean {
  if (d.status === 'failed') {
    return true
  }
  return d.status === 'uploaded' && now - new Date(d.created_at).getTime() > STUCK_MS
}

const STATUS_OPTIONS: { value: DocumentStatus; label: string }[] = [
  { value: 'uploaded', get label() { return t('排队中') } },
  { value: 'processing', get label() { return t('处理中') } },
  { value: 'done', get label() { return t('待审核') } },
  { value: 'failed', get label() { return t('失败') } },
  { value: 'reviewed', get label() { return t('已审核') } },
]

/**
 * Filters live in the URL (?status=done&q=costco), so they survive reloads, back navigation and links from
 * the dashboard.
 */
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

/**
 * Document list: upload, filter, status and progress, retry failures, select for export. Clicking a file name
 * opens the review page.
 */
export function DocumentListPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  // Selected documents (for export). Kept across pages, so selection can span pages.
  const [selected, setSelected] = useState<string[]>([])
  // Selected rows (remembered across pages): their status may change, so check for documents being processed
  // before reprocessing.
  const [pickedRows, setPickedRows] = useState<Record<string, DocumentSummary>>({})
  const [params, setParams] = useSearchParams()
  const filters = filtersFrom(params)
  const filtered = params.toString() !== ''
  // Typing in the search box does not query; press Enter or click search.
  const [search, setSearch] = useState(filters.q ?? '')
  const [valueSearch, setValueSearch] = useState(filters.value_q ?? '')
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, staleTime: Infinity })
  const folders = useQuery({ queryKey: ['folders'], queryFn: listFolders })

  // Options for "search in field": the selected template's fields, or all templates' fields when none is
  // selected (each ID listed once).
  const fieldOptions = (() => {
    const seen = new Map<string, string>()
    for (const tpl of templates.data ?? []) {
      if (filters.template && tpl.name !== filters.template) continue
      for (const f of tpl.fields) if (!seen.has(f.name)) seen.set(f.name, f.label || f.name)
    }
    return [...seen].map(([value, label]) => ({ value, label }))
  })()

  function setFilters(change: Partial<DocumentFilters>) {
    setParams(paramsFrom({ ...filters, ...change }), { replace: true })
    setPage(1) // Filters changed: start from the first page.
  }

  const documents = useQuery({
    queryKey: ['documents', page, params.toString()],
    queryFn: () => listDocuments(PAGE_SIZE, (page - 1) * PAGE_SIZE, filters),
    placeholderData: keepPreviousData, // Keep showing the previous page until new data arrives, so the table does not flicker.
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => IN_PROGRESS.includes(d.status)) ? POLL_MS : false,
  })

  const retry = useMutation({
    mutationFn: processDocument,
    onSuccess: (document) => void message.success(t('{{filename}} 已重新排队', { filename: document.filename })),
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ['documents'] }),
  })

  const remove = useMutation({
    mutationFn: deleteDocuments,
    onSuccess: ({ deleted, skipped }, ids) => {
      void message.success(t('删除了 {{deleted}} 份', { deleted }))
      for (const reason of skipped) void message.warning(t('没删：{{reason}}', { reason }))
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

  /**
   * Current status of a selected document: from the current page if present, otherwise as recorded when
   * selected.
   */
  function statusOf(id: string): DocumentStatus | undefined {
    return documents.data?.items.find((d) => d.id === id)?.status ?? pickedRows[id]?.status
  }
  const busyPicked = selected.filter((id) => IN_PROGRESS.includes(statusOf(id) as DocumentStatus))

  /** The selection contains processing or queued documents: do not reprocess; ask to deselect them. */
  function warnBusy() {
    const names = busyPicked.map(
      (id) => documents.data?.items.find((d) => d.id === id)?.filename ?? pickedRows[id]?.filename ?? id,
    )
    modal.confirm({
      title: t('有 {{length}} 份正在处理，不能重新处理', { length: names.length }),
      content: t('{{names}}{{more}}。请取消勾选后再试。', {
        names: names.slice(0, 5).join(t('、')),
        more: names.length > 5 ? t(' 等 {{length}} 份', { length: names.length }) : '',
      }),
      okText: t('取消勾选这些'),
      cancelText: t('返回'),
      onOk: () => setSelected((s) => s.filter((id) => !busyPicked.includes(id))),
    })
  }


  const columns: TableProps<DocumentSummary>['columns'] = [
    {
      title: t('文件名'),
      dataIndex: 'filename',
      ellipsis: true,
      render: (filename: string, d) => (
        <Flex gap={6} align="center" style={{ minWidth: 0 }}>
          {/* Only processed documents have results to view. */}
          <Typography.Text ellipsis style={{ minWidth: 0 }}>
            {d.status === 'done' || d.status === 'reviewed' ? (
              <Link to={`/documents/${d.id}`}>{filename}</Link>
            ) : (
              filename
            )}
          </Typography.Text>
          {d.skipped_at && (
            <Tooltip title={d.skip_reason ? t('跳过原因：{{skip_reason}}', { skip_reason: d.skip_reason }) : t('审核时先跳过了')}>
              <Tag color="orange" style={{ marginInlineEnd: 0 }}>
                {t('已跳过')}
              </Tag>
            </Tooltip>
          )}
          {(d.same_file ?? []).length > 0 && (
            <Tooltip
              title={t('内容完全一样的文件以前传过：{{files}}', {
                files: (d.same_file ?? [])
                  .map((o) => t('{{filename}}（{{formatTime}}）', { filename: o.filename, formatTime: formatTime(o.created_at) }))
                  .join(t('、')),
              })}
            >
              <Tag color="red" style={{ marginInlineEnd: 0 }}>
                {t('重复的文件')}
              </Tag>
            </Tooltip>
          )}
          {d.folder && (
            <Tooltip title={t('批量导入时所在的文件夹，点一下只看这个文件夹')}>
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
            <Tooltip title={t('{{open_comments}} 条备注没解决', { open_comments: d.open_comments })}>
              <Tag icon={<MessageOutlined />} style={{ marginInlineEnd: 0 }}>
                {d.open_comments}
              </Tag>
            </Tooltip>
          )}
        </Flex>
      ),
    },
    {
      title: t('状态'),
      dataIndex: 'status',
      width: 150,
      render: (_, d) =>
        d.status === 'processing' && d.progress !== null ? (
          // Processing: show the current step.
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
    { title: t('页数'), dataIndex: 'page_count', width: 80, align: 'right' },
    {
      title: t('待确认'),
      dataIndex: 'review_count',
      width: 110,
      align: 'right',
      render: (count: number, d) =>
        d.status !== 'done' ? '–' : count > 0 ? <Tag color="gold">{count}</Tag> : 0,
    },
    {
      title: t('大小'),
      dataIndex: 'size_bytes',
      width: 100,
      align: 'right',
      render: formatBytes,
    },
    { title: t('上传时间'), dataIndex: 'created_at', width: 170, render: formatTime },
    {
      title: t('操作'),
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
              {t('重试')}
            </Button>
          )}
          {canDelete && d.status !== 'processing' && (
            <Popconfirm
              title={t('删除「{{filename}}」？', { filename: d.filename })}
              description={t('原始文件、处理结果、审核记录、备注都会删掉，不能恢复。')}
              okText={t('删除')}
              okButtonProps={{ danger: true }}
              cancelText={t('取消')}
              onConfirm={() => remove.mutate([d.id])}
            >
              <Button size="small" danger type="text" icon={<DeleteOutlined />} aria-label={t('删除')}>
                {t('删除')}
              </Button>
            </Popconfirm>
          )}
        </Flex>
      ),
    },
  ]

  return (
    <Card
      title={t('文档')}
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
              title={t('删除选中的 {{length}} 份？', { length: selected.length })}
              description={t('原始文件、处理结果、审核记录、备注都会删掉，不能恢复。正在处理的会跳过。')}
              okText={t('删除')}
              okButtonProps={{ danger: true }}
              cancelText={t('取消')}
              disabled={selected.length === 0}
              onConfirm={() => remove.mutate(selected)}
            >
              <Tooltip title={selected.length === 0 ? t('先在左边勾选要删除的') : undefined}>
                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={selected.length === 0}
                  loading={remove.isPending}
                >
                  {t('删除')}{selected.length > 0 ? t('（{{length}}）', { length: selected.length }) : ''}
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
            {t('刷新')}
          </Button>
        </Flex>
      }
    >
      {can('document:create') && <UploadArea />}
      <Flex gap={8} wrap align="center" style={{ marginBottom: 12 }}>
        <Select<DocumentStatus[]>
          mode="multiple"
          allowClear
          placeholder={t('全部状态')}
          value={filters.status}
          onChange={(status) => setFilters({ status })}
          options={STATUS_OPTIONS}
          style={{ minWidth: 180 }}
          maxTagCount="responsive"
        />
        <Select<string>
          allowClear
          placeholder={t('全部模板')}
          value={filters.template}
          onChange={(template) => setFilters({ template })}
          options={templates.data?.map((tpl) => ({
            value: tpl.name,
            label: tpl.kind === 'builtin' ? t('{{title}}（内置）', { title: tpl.title }) : tpl.title,
          }))}
          style={{ minWidth: 160 }}
          popupMatchSelectWidth={false}
        />
        <Flex gap={4} align="center">
          <Input
            type="date"
            aria-label={t('上传日期从')}
            value={filters.date_from ?? ''}
            onChange={(e) => setFilters({ date_from: e.target.value || undefined })}
            style={{ width: 150 }}
          />
          <Typography.Text type="secondary">{t('至')}</Typography.Text>
          <Input
            type="date"
            aria-label={t('上传日期到')}
            value={filters.date_to ?? ''}
            onChange={(e) => setFilters({ date_to: e.target.value || undefined })}
            style={{ width: 150 }}
          />
        </Flex>
        <Input.Search
          allowClear
          placeholder={t('文件名')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(q) => setFilters({ q: q.trim() || undefined })}
          enterButton={<SearchOutlined />}
          style={{ width: 200 }}
        />
        {/* Keep the field selector and the search input together when wrapping. */}
        <Space.Compact>
          <Select<string>
            allowClear
            showSearch={{ optionFilterProp: 'label' }}
            placeholder={t('所有字段')}
            aria-label={t('在哪个字段里找')}
            value={filters.field}
            onChange={(field) => setFilters({ field })}
            options={fieldOptions}
            style={{ width: 140 }}
            popupMatchSelectWidth={false}
          />
          <Input.Search
            allowClear
            placeholder={t('字段内容，如发票号码、商家')}
            aria-label={t('按字段内容找')}
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
            placeholder={t('全部文件夹')}
            aria-label={t('文件夹')}
            value={filters.folder}
            onChange={(folder) => setFilters({ folder })}
            options={(folders.data ?? []).map((f) => ({
              value: f.folder,
              label: t('{{folder}}（{{count}}）', { folder: f.folder, count: f.count }),
            }))}
            style={{ minWidth: 180 }}
            popupMatchSelectWidth={false}
          />
        )}
        <Select<string>
          aria-label={t('跳过的文档')}
          value={filters.skipped === undefined ? 'all' : filters.skipped ? 'only' : 'hide'}
          onChange={(v) => setFilters({ skipped: v === 'all' ? undefined : v === 'only' })}
          options={[
            { value: 'all', label: t('跳过的也显示') },
            { value: 'only', label: t('只看跳过的') },
            { value: 'hide', label: t('不看跳过的') },
          ]}
          style={{ width: 130 }}
        />
        <Checkbox
          checked={filters.pending}
          onChange={(e) => setFilters({ pending: e.target.checked })}
        >
          {t('只看有待确认字段的')}
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
            {t('清除筛选')}
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
          preserveSelectedRowKeys: true, // Selections survive switching pages.
          // Queued and processing documents cannot be selected: no results yet (not exportable), and they
          // cannot be reprocessed or deleted; failed documents can be reprocessed and deleted.
          // Documents that started processing after being selected can still be deselected.
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
          showTotal: (total) => t('共 {{total}} 个', { total }),
          onChange: setPage,
        }}
        locale={{
          emptyText: filtered
            ? t('没有符合条件的文档')
            : can('document:create')
              ? t('还没有文档，先上传一个')
              : t('还没有文档'),
        }}
      />
    </Card>
  )
}
