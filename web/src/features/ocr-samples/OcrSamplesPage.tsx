import { DownloadOutlined, ReloadOutlined, RollbackOutlined, UndoOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Image,
  Modal,
  Radio,
  Segmented,
  Select,
  Switch,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import {
  CATEGORIES,
  type OcrSample,
  type SampleCategory,
  type SampleFilters,
  type SampleStatus,
  type SourceFormat,
  TAGS,
  downloadSamples,
  getSampleImage,
  getSampleSettings,
  listSamples,
  previewExport,
  retrySample,
  sampleTemplates,
  setSampleSettings,
  updateSample,
} from '../../api/ocrSamples'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'

const PAGE_SIZE = 20
const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }
const ORDER = Object.keys(CATEGORIES) as SampleCategory[]

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

/** 一条样本的小图：带登录 token 取回来，再变成浏览器内部地址。点开放大。 */
function SampleImage({ sample }: { sample: OcrSample }) {
  const image = useQuery({
    queryKey: ['ocr-sample-image', sample.id, sample.status === 'failed'],
    queryFn: async () => URL.createObjectURL(await getSampleImage(sample.id)),
    enabled: sample.status !== 'failed',
    staleTime: Infinity,
  })
  if (sample.status === 'failed') {
    return (
      <Tooltip title={sample.error}>
        <Tag color="error">裁图失败</Tag>
      </Tooltip>
    )
  }
  if (!image.data) {
    return <div style={{ height: 36, width: 120, background: '#fafafa' }} />
  }
  return (
    <Image
      src={image.data}
      alt="样本小图"
      height={36}
      style={{ maxWidth: 280, objectFit: 'contain', objectPosition: 'left', border: '1px solid #f0f0f0' }}
    />
  )
}

/** 开关：只有管理员能改。 */
function CollectSwitch() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
  const change = useMutation({
    mutationFn: setSampleSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['ocr-sample-settings'], data)
      void message.success(data.collect ? '已打开：审核时会收集 OCR 样本' : '已关掉，不再收集新样本')
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const on = settings.data?.collect ?? false
  const toggle = (
    <Switch
      checked={on}
      loading={settings.isLoading || change.isPending}
      disabled={!settings.data?.can_change}
      onChange={(checked) => change.mutate(checked)}
    />
  )
  return (
    <Card size="small">
      <Flex gap={16} align="flex-start">
        {settings.data?.can_change ? toggle : <Tooltip title="只有管理员能改">{toggle}</Tooltip>}
        <Flex vertical gap={4} style={{ flex: 1 }}>
          <Typography.Text strong>自动收集纠错样本{on ? '（开着）' : '（关着）'}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            打开后，审核页点一块原文会先弹出「图中原文」：按图上原样改对，回车填入。保存审核的同时，系统把这一行裁成小图，
            连同图上原样的文字存成一条样本；框选重新识别的也一样。攒够了可以导出，用 PaddleOCR 微调文字识别模型。
            敏感字段、PDF 自带的文字不收集。关着时审核和以前一样。
          </Typography.Text>
        </Flex>
      </Flex>
    </Card>
  )
}

/** 导出：选范围、看预览（会导出几条、不导出的原因），下载 ZIP。 */
function ExportDialog({ open, onClose, templates }: { open: boolean; onClose: () => void; templates: string[] }) {
  const { message } = App.useApp()
  const [categories, setCategories] = useState<SampleCategory[]>(['rec_error', 'missed', 'correct'])
  const [format, setFormat] = useState<SourceFormat | ''>('')
  const [template, setTemplate] = useState<string | undefined>()
  const filters: SampleFilters = {
    category: categories,
    source_format: format || undefined,
    template,
  }
  const preview = useQuery({
    queryKey: ['ocr-sample-preview', filters],
    queryFn: () => previewExport(filters),
    enabled: open && categories.length > 0,
  })
  const download = useMutation({
    mutationFn: () => downloadSamples(filters),
    onSuccess: ({ blob, filename }) => {
      saveFile(blob, filename)
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = preview.data
  const ready = categories.length > 0 && (data?.included ?? 0) > 0
  return (
    <Modal
      title="导出 OCR 样本"
      open={open}
      onCancel={onClose}
      width={560}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button
          key="ok"
          type="primary"
          icon={<DownloadOutlined />}
          disabled={!ready}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          下载 ZIP{data && categories.length > 0 ? `（${data.included} 条）` : ''}
        </Button>,
      ]}
    >
      <Flex vertical gap={16}>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          PaddleOCR 识别训练的格式：<Typography.Text code>crop_img/</Typography.Text> 小图、
          <Typography.Text code>rec_gt.txt</Typography.Text>（图片路径、Tab、图中原文）、
          <Typography.Text code>manifest.jsonl</Typography.Text>（OCR 当时读的、分类……）和 README。
          不带原文件、文件名和审核人。这是你的业务数据，不要放进代码仓库。
        </Typography.Text>
        <div>
          <Typography.Text strong>分类</Typography.Text>
          <Checkbox.Group
            style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}
            value={categories}
            onChange={(v) => setCategories(v as SampleCategory[])}
          >
            {ORDER.map((c) => (
              <Checkbox key={c} value={c}>
                {CATEGORIES[c].name}
                <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                  {CATEGORIES[c].hint}
                </Typography.Text>
              </Checkbox>
            ))}
          </Checkbox.Group>
        </div>
        <Flex gap={24} wrap>
          <div>
            <Typography.Text strong>来源　</Typography.Text>
            <Radio.Group
              value={format}
              onChange={(e) => setFormat(e.target.value as SourceFormat | '')}
              options={[
                { value: '', label: '全部' },
                { value: 'pdf', label: 'PDF' },
                { value: 'image', label: '照片' },
              ]}
            />
          </div>
          <Select
            style={{ minWidth: 180 }}
            allowClear
            placeholder="全部模板"
            value={template}
            onChange={setTemplate}
            options={templates.map((t) => ({ value: t, label: t }))}
          />
        </Flex>
        {categories.length === 0 ? (
          <Alert type="warning" showIcon title="至少选一种分类" />
        ) : preview.isError ? (
          <Alert type="error" showIcon title={errorText(preview.error)} />
        ) : (
          data && (
            <Alert
              type={data.included > 0 ? 'info' : 'warning'}
              showIcon
              title={
                data.included > 0
                  ? `会导出 ${data.included} 条：${ORDER.filter((c) => data.categories[c])
                      .map((c) => `${CATEGORIES[c].name} ${data.categories[c]}`)
                      .join('，')}`
                  : '按这个范围没有能导出的样本'
              }
              description={
                data.excluded.length > 0
                  ? `不导出：${data.excluded.map((e) => `${e.reason} ${e.count} 条`).join('；')}`
                  : undefined
              }
            />
          )
        )}
        {categories.includes('rec_error') && !categories.includes('correct') && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只用错例微调，模型容易把原来认得好的字"忘掉"。建议把「认对的行」一起导出。
          </Typography.Text>
        )}
      </Flex>
    </Modal>
  )
}

/** OCR 样本页（ADR 0050）：开关、样本列表（看图、改原文和分类、撤回）、导出 ZIP。 */
export function OcrSamplesPage() {
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const canReview = can('value:review')
  const [category, setCategory] = useState<SampleCategory | ''>('')
  const [status, setStatus] = useState<SampleStatus | ''>('')
  const [format, setFormat] = useState<SourceFormat | ''>('')
  const [template, setTemplate] = useState<string | undefined>()
  const [pageNo, setPageNo] = useState(1)
  const [exporting, setExporting] = useState(false)
  const filters: SampleFilters = {
    category: category ? [category] : undefined,
    status: status || undefined,
    source_format: format || undefined,
    template,
  }
  const list = useQuery({
    queryKey: ['ocr-samples', filters, pageNo],
    queryFn: () => listSamples(filters, PAGE_SIZE, (pageNo - 1) * PAGE_SIZE),
    placeholderData: keepPreviousData,
  })
  const templates = useQuery({ queryKey: ['ocr-sample-templates'], queryFn: sampleTemplates })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['ocr-samples'] })
    void queryClient.invalidateQueries({ queryKey: ['ocr-sample-preview'] })
  }
  const update = useMutation({
    mutationFn: ({ id, change }: { id: string; change: Parameters<typeof updateSample>[1] }) =>
      updateSample(id, change),
    onSuccess: refresh,
    onError: (err) => void message.error(errorText(err)),
  })
  const retry = useMutation({
    mutationFn: retrySample,
    onSuccess: () => {
      refresh()
      void message.success('裁好了')
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = list.data
  const counts = data?.categories ?? {}
  const all = ORDER.reduce((sum, c) => sum + (counts[c] ?? 0), 0)
  const filtered = Boolean(category || status || format || template)
  const reset = (apply: () => void) => {
    apply()
    setPageNo(1)
  }

  const columns: TableProps<OcrSample>['columns'] = [
    { title: '小图', key: 'image', width: 300, render: (_, s) => <SampleImage sample={s} /> },
    {
      title: 'OCR 读的',
      dataIndex: 'ocr_text',
      render: (text: string | null) =>
        text === null ? (
          <Typography.Text type="secondary">（这里没有文字块）</Typography.Text>
        ) : text === '' ? (
          <Typography.Text type="secondary">（没认出）</Typography.Text>
        ) : (
          <Typography.Text type="secondary" style={MONO}>
            {text}
          </Typography.Text>
        ),
    },
    {
      title: <Tooltip title="图上原样的文字，训练用的标签">图中原文</Tooltip>,
      dataIndex: 'transcription',
      render: (text: string, s) => (
        <Typography.Text
          style={MONO}
          editable={
            canReview
              ? {
                  tooltip: '改图中原文',
                  onChange: (next) => {
                    if (next.trim() && next !== text) update.mutate({ id: s.id, change: { transcription: next } })
                  },
                }
              : false
          }
        >
          {text}
        </Typography.Text>
      ),
    },
    {
      title: '分类',
      key: 'category',
      width: 170,
      render: (_, s) => (
        <Flex vertical gap={4} align="flex-start">
          {canReview ? (
            <Select
              size="small"
              style={{ width: 150 }}
              value={s.category}
              onChange={(c) => update.mutate({ id: s.id, change: { category: c } })}
              options={ORDER.map((c) => ({ value: c, label: CATEGORIES[c].name }))}
              labelRender={({ value }) => (
                <Tag color={CATEGORIES[value as SampleCategory].color} style={{ marginInlineEnd: 0 }}>
                  {CATEGORIES[value as SampleCategory].name}
                </Tag>
              )}
            />
          ) : (
            <Tag color={CATEGORIES[s.category].color}>{CATEGORIES[s.category].name}</Tag>
          )}
          {s.error_tags.length > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {s.error_tags.map((t) => TAGS[t] ?? t).join('、')}
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    {
      title: '字段',
      key: 'field',
      render: (_, s) => (
        <Flex vertical>
          <span>
            {s.field_label}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {' '}
              · {s.template}
            </Typography.Text>
          </span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            <Link to={`/documents/${s.document_id}`}>{s.filename}</Link> · 第 {s.page} 页 ·{' '}
            {s.source_format === 'pdf' ? 'PDF' : '照片'}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: '收集时间',
      dataIndex: 'created_at',
      width: 150,
      render: (t: string) => <Typography.Text type="secondary">{formatTime(t)}</Typography.Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 96,
      render: (_, s) =>
        !canReview ? null : s.status === 'failed' ? (
          <Button size="small" icon={<ReloadOutlined />} loading={retry.isPending} onClick={() => retry.mutate(s.id)}>
            重试
          </Button>
        ) : s.status === 'withdrawn' ? (
          <Button size="small" icon={<UndoOutlined />} onClick={() => update.mutate({ id: s.id, change: { status: 'ready' } })}>
            恢复
          </Button>
        ) : (
          <Tooltip title="撤回：不导出这一条（可以恢复）">
            <Button
              size="small"
              type="text"
              icon={<RollbackOutlined />}
              onClick={() => update.mutate({ id: s.id, change: { status: 'withdrawn' } })}
            >
              撤回
            </Button>
          </Tooltip>
        ),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          OCR 样本
        </Typography.Title>
        {can('document:export') && (
          <Button type="primary" icon={<DownloadOutlined />} disabled={all === 0} onClick={() => setExporting(true)}>
            导出 ZIP
          </Button>
        )}
      </Flex>
      <CollectSwitch />
      <Card size="small">
        <Flex vertical gap={12}>
          <Flex justify="space-between" align="center" wrap gap={8}>
            <Segmented
              value={category}
              onChange={(v) => reset(() => setCategory(v as SampleCategory | ''))}
              options={[
                { value: '', label: `全部 ${all}` },
                ...ORDER.map((c) => ({
                  value: c,
                  label: (
                    <Tooltip title={CATEGORIES[c].hint}>
                      {CATEGORIES[c].name} {counts[c] ?? 0}
                    </Tooltip>
                  ),
                })),
              ]}
            />
            <Flex gap={8} wrap>
              <Select
                style={{ width: 130 }}
                value={status}
                onChange={(v) => reset(() => setStatus(v))}
                options={[
                  { value: '', label: '全部状态' },
                  { value: 'ready', label: `可以导出 ${data?.statuses.ready ?? 0}` },
                  { value: 'failed', label: `裁图失败 ${data?.statuses.failed ?? 0}` },
                  { value: 'withdrawn', label: `撤回了 ${data?.statuses.withdrawn ?? 0}` },
                ]}
              />
              <Select
                style={{ width: 110 }}
                value={format}
                onChange={(v) => reset(() => setFormat(v))}
                options={[
                  { value: '', label: '全部来源' },
                  { value: 'pdf', label: 'PDF' },
                  { value: 'image', label: '照片' },
                ]}
              />
              <Select
                style={{ minWidth: 160 }}
                allowClear
                placeholder="全部模板"
                value={template}
                onChange={(v) => reset(() => setTemplate(v))}
                options={(templates.data ?? []).map((t) => ({ value: t, label: t }))}
              />
            </Flex>
          </Flex>
          {list.isError && <Alert type="error" showIcon title="样本读不出来" description={errorText(list.error)} />}
          <Table
            rowKey="id"
            size="middle"
            loading={list.isLoading}
            columns={columns}
            dataSource={data?.items ?? []}
            onRow={(s) => ({ style: s.status === 'withdrawn' ? { opacity: 0.5 } : undefined })}
            scroll={{ x: 1000 }}
            pagination={{
              current: pageNo,
              pageSize: PAGE_SIZE,
              total: data?.total ?? 0,
              showSizeChanger: false,
              showTotal: (n) => `共 ${n} 条`,
              onChange: setPageNo,
            }}
            locale={{
              emptyText: (
                <Empty
                  description={
                    filtered
                      ? '没有符合条件的样本'
                      : '还没有样本。打开上面的开关后，审核时核对「图中原文」并保存，就会收集。'
                  }
                />
              ),
            }}
          />
        </Flex>
      </Card>
      <ExportDialog open={exporting} onClose={() => setExporting(false)} templates={templates.data ?? []} />
    </Flex>
  )
}
