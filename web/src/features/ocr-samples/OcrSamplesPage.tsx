import { CheckOutlined, DatabaseOutlined, DownloadOutlined, HistoryOutlined, ReloadOutlined, RollbackOutlined, UndoOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Collapse,
  Descriptions,
  Empty,
  Flex,
  Image,
  Input,
  Modal,
  Radio,
  Segmented,
  Select,
  Switch,
  Table,
  Tabs,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../../api/client'
import {
  CATEGORIES,
  type ExportKind,
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
  backfillSamples,
  collectConfirmed,
  verifySamples,
  retrySample,
  saveSamplesAsDataset,
  sampleTemplates,
  setSampleSettings,
  updateSample,
} from '../../api/ocrSamples'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'
import { DetPagesTab } from './DetPagesTab'
import { ExportsTab } from './ExportsTab'
import { GroupsTab } from './GroupsTab'
import { useTranslation } from 'react-i18next'

const PAGE_SIZE = 20
const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }
const ORDER = Object.keys(CATEGORIES) as SampleCategory[]

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

/** Crop of a sample: fetched with the login token and turned into an object URL. Click to enlarge. */
function SampleImage({ sample }: { sample: OcrSample }) {
  const { t } = useTranslation()
  const image = useQuery({
    queryKey: ['ocr-sample-image', sample.id, sample.status === 'failed'],
    queryFn: async () => URL.createObjectURL(await getSampleImage(sample.id)),
    enabled: sample.status !== 'failed',
    staleTime: Infinity,
  })
  if (sample.status === 'failed') {
    return (
      <Tooltip title={sample.error}>
        <Tag color="error">{t('裁图失败')}</Tag>
      </Tooltip>
    )
  }
  if (!image.data) {
    return <div style={{ height: 36, width: 120, background: '#fafafa' }} />
  }
  return (
    <Image
      src={image.data}
      alt={t('样本小图')}
      height={36}
      style={{ maxWidth: 280, objectFit: 'contain', objectPosition: 'left', border: '1px solid #f0f0f0' }}
    />
  )
}

/** Switch: administrators only. */
function CollectSwitch() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
  const change = useMutation({
    mutationFn: setSampleSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['ocr-sample-settings'], data)
      void message.success(data.collect ? t('已打开：审核时会收集 OCR 样本') : t('已关掉，不再收集新样本'))
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
        {settings.data?.can_change ? toggle : <Tooltip title={t('只有管理员能改')}>{toggle}</Tooltip>}
        <Flex vertical gap={4} style={{ flex: 1 }}>
          <Typography.Text strong>{t('自动收集纠错样本')}{on ? t('（开着）') : t('（关着）')}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('打开后，审核页点一块原文会先弹出「图中原文」：按图上原样改对，回车填入。保存审核的同时，系统把这一行裁成小图， 连同图上原样的文字存成一条样本；框选重新识别的也一样。攒够了可以导出，用 PaddleOCR 微调文字识别模型。 敏感字段、PDF 自带的文字不收集。关着时审核和以前一样。')}
          </Typography.Text>
        </Flex>
      </Flex>
    </Card>
  )
}

/**
 * Export: choose the scope, preview (groups and lines in training and validation, sufficiency, excluded
 * reasons) and download the ZIP.
 */
function ExportDialog({ open, onClose, templates }: { open: boolean; onClose: () => void; templates: string[] }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const { can } = useAuth()
  const canStore = can('value:review')
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<ExportKind>('train_val')
  const [categories, setCategories] = useState<SampleCategory[]>(['rec_error', 'missed', 'correct'])
  const [format, setFormat] = useState<SourceFormat | ''>('')
  const [template, setTemplate] = useState<string | undefined>()
  const [datasetName, setDatasetName] = useState('')
  const filters: SampleFilters = {
    category: categories,
    source_format: format || undefined,
    template,
  }
  const preview = useQuery({
    queryKey: ['ocr-sample-preview', filters, kind],
    queryFn: () => previewExport(filters, kind),
    enabled: open && categories.length > 0,
  })
  const download = useMutation({
    mutationFn: () => downloadSamples(filters, kind),
    onSuccess: ({ blob, filename }) => {
      saveFile(blob, filename)
      void queryClient.invalidateQueries({ queryKey: ['ocr-exports'] })
      void queryClient.invalidateQueries({ queryKey: ['ocr-groups'] })
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const store = useMutation({
    mutationFn: () =>
      saveSamplesAsDataset(filters, kind, { name: datasetName.trim(), description: t('OCR 样本导出') }),
    onSuccess: (d) => {
      void message.success(t('已存进数据集库：{{name}} v{{version}}，可以在“模型和数据集”页拿它比较识别模型', { name: d.name, version: d.version }))
      void queryClient.invalidateQueries({ queryKey: ['ocr-exports'] })
      void queryClient.invalidateQueries({ queryKey: ['ocr-groups'] })
      void queryClient.invalidateQueries({ queryKey: ['hub-datasets'] })
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = preview.data
  const ready = categories.length > 0 && (data?.included ?? 0) > 0
  const parts = kind === 'test' ? (['test'] as const) : (['train', 'val'] as const)
  const partNames = { train: t('训练集'), val: t('验证集'), test: t('锁定测试集') }
  const evidenceType = { process: 'warning', thin: 'warning', trend: 'success', none: 'info' } as const
  return (
    <Modal
      title={t('导出 OCR 样本')}
      open={open}
      onCancel={onClose}
      width={620}
      footer={[
        <Button key="cancel" onClick={onClose}>
          {t('取消')}
        </Button>,
        <Tooltip key="dataset" title={datasetName.trim() ? '' : t('先在下面填数据集的名字')}>
          <Button
            icon={<DatabaseOutlined />}
            disabled={!ready || !datasetName.trim() || !canStore}
            loading={store.isPending}
            onClick={() => store.mutate()}
          >
            {t('存进数据集库')}
          </Button>
        </Tooltip>,
        <Button
          key="ok"
          type="primary"
          icon={<DownloadOutlined />}
          disabled={!ready}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          {t('下载 ZIP')}{data && categories.length > 0 ? t('（{{included}} 条）', { included: data.included }) : ''}
        </Button>,
      ]}
    >
      <Flex vertical gap={16}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={kind}
          onChange={(e) => setKind(e.target.value as ExportKind)}
          options={[
            { value: 'train_val', label: t('训练集和验证集') },
            { value: 'test', label: t('锁定测试集') },
          ]}
        />
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {kind === 'test' ? (
            <>
              {t('只导出锁定成测试集的组（')}<Typography.Text code>rec_gt_test.txt</Typography.Text>{t('）。正式验收用它，平时别反复看。')}
            </>
          ) : (
            <>
              {t('PaddleOCR 识别训练的格式：')}<Typography.Text code>crop_img/</Typography.Text>{t('、')}
              <Typography.Text code>rec_gt_train.txt</Typography.Text>{t('、')}<Typography.Text code>rec_gt_val.txt</Typography.Text>
              {t('（同一张单据整组在同一边）、')}<Typography.Text code>manifest.jsonl</Typography.Text>{' '}{t('和 README。')}
            </>
          )}
          {t('不带原文件、文件名和审核人。这是你的业务数据，不要放进代码仓库。')}
        </Typography.Text>
        <div>
          <Typography.Text strong>{t('分类')}</Typography.Text>
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
            <Typography.Text strong>{t('来源')}{' '}</Typography.Text>
            <Radio.Group
              value={format}
              onChange={(e) => setFormat(e.target.value as SourceFormat | '')}
              options={[
                { value: '', label: t('全部') },
                { value: 'pdf', label: 'PDF' },
                { value: 'image', label: t('照片') },
              ]}
            />
          </div>
          <Select
            style={{ minWidth: 180 }}
            allowClear
            placeholder={t('全部模板')}
            value={template}
            onChange={setTemplate}
            options={templates.map((templateName) => ({ value: templateName, label: templateName }))}
          />
        </Flex>
        {categories.length === 0 ? (
          <Alert type="warning" showIcon title={t('至少选一种分类')} />
        ) : preview.isError ? (
          <Alert type="error" showIcon title={errorText(preview.error)} />
        ) : (
          data && (
            <Flex vertical gap={8}>
              <Descriptions
                size="small"
                bordered
                column={parts.length}
                items={parts.map((p) => ({
                  key: p,
                  label: partNames[p],
                  children: t('{{value}} 组 · {{value2}} 行', { value: data.splits[p]?.groups ?? 0, value2: data.splits[p]?.rows ?? 0 }),
                }))}
              />
              {data.evidence && (
                <Alert
                  type={evidenceType[data.evidence.level]}
                  showIcon
                  title={data.evidence.message}
                  description={
                    Object.keys(data.val_category_groups).length > 0
                      ? t('验证集里：{{groups}}。至少 10 组、50 行才不只是检查流程；15 组、100 行以上才看得出趋势。', {
                          groups: ORDER.filter((c) => data.val_category_groups[c])
                            .map((c) => t('{{name}}来自 {{value}} 组', { name: CATEGORIES[c].name, value: data.val_category_groups[c] }))
                            .join(t('，')),
                        })
                      : t('至少 10 组、50 行才不只是检查流程；15 组、100 行以上才看得出趋势。')
                  }
                />
              )}
              {data.paddle_minimum && kind === 'train_val' && data.included < data.paddle_minimum && (
                <Alert
                  type="info"
                  showIcon
                  title={t('PaddleOCR 微调建议至少 {{paddle_minimum}} 行，现在 {{included}} 行，还差 {{value}} 行', { paddle_minimum: data.paddle_minimum, included: data.included, value: data.paddle_minimum - data.included })}
                  description={t('可以先导出看流程；正式训练前多攒一些（审核时收集、补收、收认对的行），或按 README 用真实语料合成补充。')}
                />
              )}
              <Alert
                type={data.included > 0 ? 'info' : 'warning'}
                showIcon
                title={
                  data.included > 0
                    ? t('会导出 {{included}} 条：{{categories}}', {
                        included: data.included,
                        categories: ORDER.filter((c) => data.categories[c])
                          .map((c) => `${CATEGORIES[c].name} ${data.categories[c]}`)
                          .join(t('，')),
                      })
                    : t('按这个范围没有能导出的样本')
                }
                description={
                  data.excluded.length > 0
                    ? t('不导出：{{reasons}}', {
                        reasons: data.excluded.map((e) => t('{{reason}} {{count}} 条', { reason: e.reason, count: e.count })).join(t('；')),
                      })
                    : undefined
                }
              />
              {data.distributions.length > 0 && (
                <Collapse
                  size="small"
                  items={[
                    {
                      key: 'dist',
                      label: t('分布（只报告，不为凑比例挪组）'),
                      children: (
                        <Table
                          size="small"
                          pagination={false}
                          rowKey={(d) => `${d.by}-${d.key}`}
                          dataSource={data.distributions}
                          columns={[
                            {
                              title: t('按'),
                              dataIndex: 'by',
                              width: 70,
                              render: (by: string) => ({ template: t('模板'), category: t('分类'), source_format: t('来源') })[by] ?? by,
                            },
                            {
                              title: '',
                              dataIndex: 'key',
                              render: (k: string) =>
                                ({ pdf: 'PDF', image: t('照片') } as Record<string, string>)[k] ??
                                CATEGORIES[k as SampleCategory]?.name ??
                                k,
                            },
                            ...parts.map((p) => ({
                              title: partNames[p],
                              dataIndex: p,
                              width: 90,
                              align: 'right' as const,
                            })),
                          ]}
                        />
                      ),
                    },
                  ]}
                />
              )}
            </Flex>
          )
        )}
        {canStore && (
          <Input
            value={datasetName}
            onChange={(e) => setDatasetName(e.target.value)}
            placeholder={t('存进数据集库（给 OCR 模型评测用）时的名字，例如 收据识别')}
            maxLength={64}
          />
        )}
        {kind === 'train_val' && categories.includes('rec_error') && !categories.includes('correct') && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('只用错例微调，模型容易把原来认得好的字"忘掉"。建议把「认对的行」一起导出。')}
          </Typography.Text>
        )}
      </Flex>
    </Modal>
  )
}

/**
 * OCR samples page: switch, sample list (view crops, edit transcription and category, withdraw), ZIP export.
 */
export function OcrSamplesPage() {
  const { t } = useTranslation()
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
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') ?? 'samples'
  const setTab = (key: string) => setSearchParams(key === 'samples' ? {} : { tab: key }, { replace: true })
  const settings = useQuery({ queryKey: ['ocr-sample-settings'], queryFn: getSampleSettings })
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
      void message.success(t('裁好了'))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const backfill = useMutation({
    mutationFn: backfillSamples,
    onSuccess: (r) => {
      refresh()
      void queryClient.invalidateQueries({ queryKey: ['ocr-sample-templates'] })
      if (r.created > 0) {
        void message.success(t('补收了 {{created}} 条，在"待复核"里：对着小图确认图里的原文', { created: r.created }))
        reset(() => setStatus('pending'))
      } else {
        void message.info(
          r.skipped.length
            ? t('没有能补收的（{{reasons}}）', { reasons: r.skipped.map((s) => `${s.reason} ${s.count}`).join(t('；')) })
            : t('没有能补收的'),
        )
      }
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const confirmed = useMutation({
    mutationFn: collectConfirmed,
    onSuccess: (r) => {
      refresh()
      if (r.created > 0) {
        void message.success(t('收了 {{created}} 条认对的行，在"待复核"里：看过小图再通过', { created: r.created }))
        reset(() => setStatus('pending'))
      } else {
        void message.info(t('没有新的认对的行可收'))
      }
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const verifyPage = useMutation({
    mutationFn: verifySamples,
    onSuccess: (r) => {
      refresh()
      void message.success(t('通过了 {{verified}} 条', { verified: r.verified }))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = list.data
  const counts = data?.categories ?? {}
  const pendingHere = (data?.items ?? []).filter((s) => s.status === 'pending')
  const all = ORDER.reduce((sum, c) => sum + (counts[c] ?? 0), 0)
  const filtered = Boolean(category || status || format || template)
  const reset = (apply: () => void) => {
    apply()
    setPageNo(1)
  }

  const columns: TableProps<OcrSample>['columns'] = [
    { title: t('小图'), key: 'image', width: 300, render: (_, s) => <SampleImage sample={s} /> },
    {
      title: t('OCR 读的'),
      dataIndex: 'ocr_text',
      render: (text: string | null) =>
        text === null ? (
          <Typography.Text type="secondary">{t('（这里没有文字块）')}</Typography.Text>
        ) : text === '' ? (
          <Typography.Text type="secondary">{t('（没认出）')}</Typography.Text>
        ) : (
          <Typography.Text type="secondary" style={MONO}>
            {text}
          </Typography.Text>
        ),
    },
    {
      title: <Tooltip title={t('图上原样的文字，训练用的标签')}>{t('图中原文')}</Tooltip>,
      dataIndex: 'transcription',
      render: (text: string, s) => (
        <Flex vertical gap={2}>
        {s.status === 'pending' && (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            {s.origin === 'confirmed'
              ? t('自动收的认对的行：小图里的字和下面一样就通过，不一样先改')
              : s.reviewed_value
                ? t('补收的，先猜的；当时字段改成了 {{reviewed_value}}。对着小图写出图里的原文', { reviewed_value: s.reviewed_value })
                : t('补收的，先猜的。对着小图写出图里的原文')}
          </Typography.Text>
        )}
        <Typography.Text
          style={MONO}
          editable={
            canReview
              ? {
                  tooltip: t('改图中原文'),
                  onChange: (next) => {
                    if (next.trim() && next !== text) update.mutate({ id: s.id, change: { transcription: next } })
                  },
                }
              : false
          }
        >
          {text}
        </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('分类'),
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
              {s.error_tags.map((tag) => TAGS[tag] ?? tag).join(t('、'))}
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    {
      title: t('字段'),
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
            <Link to={`/documents/${s.document_id}`}>{s.filename}</Link>{' '}{t('· 第 {{page}} 页 ·', { page: s.page })}{' '}
            {s.source_format === 'pdf' ? 'PDF' : t('照片')}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('收集时间'),
      dataIndex: 'created_at',
      width: 150,
      render: (at: string) => <Typography.Text type="secondary">{formatTime(at)}</Typography.Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 96,
      render: (_, s) =>
        !canReview ? null : s.status === 'pending' ? (
          <Flex vertical gap={4}>
            <Tooltip title={t('图中原文对了就通过（不对先点原文旁边的笔改），通过后才能导出')}>
              <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => update.mutate({ id: s.id, change: { status: 'ready' } })}>
                {t('通过')}
              </Button>
            </Tooltip>
            <Button
              size="small"
              type="text"
              icon={<RollbackOutlined />}
              onClick={() => update.mutate({ id: s.id, change: { status: 'withdrawn' } })}
            >
              {t('不要')}
            </Button>
          </Flex>
        ) : s.status === 'failed' ? (
          <Button size="small" icon={<ReloadOutlined />} loading={retry.isPending} onClick={() => retry.mutate(s.id)}>
            {t('重试')}
          </Button>
        ) : s.status === 'withdrawn' ? (
          <Button size="small" icon={<UndoOutlined />} onClick={() => update.mutate({ id: s.id, change: { status: 'ready' } })}>
            {t('恢复')}
          </Button>
        ) : (
          <Tooltip title={t('撤回：不导出这一条（可以恢复）')}>
            <Button
              size="small"
              type="text"
              icon={<RollbackOutlined />}
              onClick={() => update.mutate({ id: s.id, change: { status: 'withdrawn' } })}
            >
              {t('撤回')}
            </Button>
          </Tooltip>
        ),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t('OCR 样本')}
        </Typography.Title>
        <Flex gap={8} wrap>
          {canReview && (
            <Tooltip title={t('审核完成的单据里，人没改过、出处正好一整块且只有这个值的：OCR 读对了。训练要有认对的行，只学错例会把认得好的字忘掉')}>
              <Button icon={<CheckOutlined />} loading={confirmed.isPending} onClick={() => confirmed.mutate()}>
                {t('收认对的行')}
              </Button>
            </Tooltip>
          )}
          {canReview && (
            <Tooltip title={t('开关打开之前审核过的：点了原文或框了一块、改了值的，裁出小图进待复核。手打的不收，同一次修改不会重复收')}>
              <Button icon={<HistoryOutlined />} loading={backfill.isPending} onClick={() => backfill.mutate()}>
                {t('从以前的修改补收')}
              </Button>
            </Tooltip>
          )}
          {can('document:export') && (
            <Button type="primary" icon={<DownloadOutlined />} disabled={all === 0} onClick={() => setExporting(true)}>
              {t('导出 ZIP')}
            </Button>
          )}
        </Flex>
      </Flex>
      <CollectSwitch />
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'samples',
            label: t('样本 {{value}}', { value: data?.total ?? '' }),
            children: (
          <Card size="small">
            <Flex vertical gap={12}>
              <Flex justify="space-between" align="center" wrap gap={8}>
                <Segmented
                  value={category}
                  onChange={(v) => reset(() => setCategory(v as SampleCategory | ''))}
                  options={[
                    { value: '', label: t('全部 {{all}}', { all }) },
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
                    style={{ width: 140 }}
                    value={status}
                    onChange={(v) => reset(() => setStatus(v))}
                    options={[
                      { value: '', label: t('全部状态') },
                      { value: 'ready', label: t('可以导出 {{value}}', { value: data?.statuses.ready ?? 0 }) },
                      { value: 'pending', label: t('待复核 {{value}}', { value: data?.statuses.pending ?? 0 }) },
                      { value: 'failed', label: t('裁图失败 {{value}}', { value: data?.statuses.failed ?? 0 }) },
                      { value: 'withdrawn', label: t('撤回了 {{value}}', { value: data?.statuses.withdrawn ?? 0 }) },
                    ]}
                  />
                  <Select
                    style={{ width: 110 }}
                    value={format}
                    onChange={(v) => reset(() => setFormat(v))}
                    options={[
                      { value: '', label: t('全部来源') },
                      { value: 'pdf', label: 'PDF' },
                      { value: 'image', label: t('照片') },
                    ]}
                  />
                  <Select
                    style={{ minWidth: 160 }}
                    allowClear
                    placeholder={t('全部模板')}
                    value={template}
                    onChange={(v) => reset(() => setTemplate(v))}
                    options={(templates.data ?? []).map((templateName) => ({ value: templateName, label: templateName }))}
                  />
                </Flex>
              </Flex>
              {canReview && status === 'pending' && pendingHere.length > 0 && (
                <Flex justify="space-between" align="center" gap={8} wrap>
                  <Typography.Text type="secondary">{t('逐张看过这一页的小图和"图中原文"，都对就一起通过；有不对的先改或点"不要"。')}</Typography.Text>
                  <Button
                    type="primary"
                    icon={<CheckOutlined />}
                    loading={verifyPage.isPending}
                    onClick={() => verifyPage.mutate(pendingHere.map((s) => s.id))}
                  >
                    {t('通过这一页（{{length}} 条）', { length: pendingHere.length })}
                  </Button>
                </Flex>
              )}
              {list.isError && <Alert type="error" showIcon title={t('样本读不出来')} description={errorText(list.error)} />}
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
                  showTotal: (n) => t('共 {{n}} 条', { n }),
                  onChange: setPageNo,
                }}
                locale={{
                  emptyText: (
                    <Empty
                      description={
                        filtered
                          ? t('没有符合条件的样本')
                          : t('还没有样本。打开上面的开关后，审核时核对「图中原文」并保存，就会收集。')
                      }
                    />
                  ),
                }}
              />
            </Flex>
          </Card>
            ),
          },
          {
            key: 'det',
            label: t('检测样本'),
            children: <DetPagesTab canExport={can('document:export')} />,
          },
          {
            key: 'groups',
            label: t('单据分组'),
            children: <GroupsTab canChange={settings.data?.can_change ?? false} canReview={canReview} />,
          },
          ...(can('document:export')
            ? [{ key: 'exports', label: t('导出记录'), children: <ExportsTab /> }]
            : []),
        ]}
      />
      <ExportDialog open={exporting} onClose={() => setExporting(false)} templates={templates.data ?? []} />
    </Flex>
  )
}
