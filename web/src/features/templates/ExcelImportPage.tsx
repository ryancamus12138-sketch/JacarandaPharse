import { ArrowLeftOutlined, CheckOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Flex,
  Input,
  Result,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { buildFromExcel, reanalyzeExcel } from '../../api/templates'
import type {
  ColumnSource,
  ExcelAnalysis,
  ExcelColumn,
  ExcelSheet,
  SheetRole,
} from '../../api/types'
import { makesField, ROLE_TEXT, SOURCE_TEXT } from './excelLabels'
import { CATEGORIES, FIELD_ID, TYPE_OPTIONS } from './templateFields'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

/** Column sources available for each sheet role. */
const SOURCES: Record<SheetRole, ColumnSource[]> = {
  document: ['extract', 'manual', 'auto', 'lookup', 'none'],
  entity: ['extract', 'manual', 'none'],
  items: ['extract', 'manual', 'doc', 'auto', 'lookup', 'none'],
  reference: [],
  options: [],
  ignore: [],
}

/**
 * Preview for creating a template from Excel: the guessed role of each sheet and source of each column,
 * reviewed and corrected before creating the template. Later exports fill data back into this workbook as
 * defined here.
 */
export function ExcelImportPage() {
  const { t } = useTranslation()
  const { uploadId = '' } = useParams()
  const analysis = useQuery({
    queryKey: ['excel-analysis', uploadId],
    queryFn: () => reanalyzeExcel(uploadId),
    staleTime: Infinity,
  })
  if (analysis.isPending) {
    return (
      <Flex justify="center" style={{ padding: 96 }}>
        <Spin size="large" />
      </Flex>
    )
  }
  if (analysis.error) {
    const text = analysis.error instanceof ApiError ? analysis.error.message : String(analysis.error)
    return (
      <Result
        status="warning"
        title={t('打不开这个 Excel')}
        subTitle={text}
        extra={
          <Link to="/templates">
            <Button>{t('回模板列表')}</Button>
          </Link>
        }
      />
    )
  }
  return <Preview analysis={analysis.data} uploadId={uploadId} />
}

function Preview({ analysis, uploadId }: { analysis: ExcelAnalysis; uploadId: string }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [sheets, setSheets] = useState<ExcelSheet[]>(analysis.sheets)
  const [title, setTitle] = useState(analysis.filename.replace(/\.[^.]+$/, ''))
  const [category, setCategory] = useState(
    // i18n-ignore-next-line: template category values are template content
    analysis.starter_title ? (analysis.starter.startsWith('receipt') ? '收据' : '发票') : '其他',
  )
  const [description, setDescription] = useState('')

  const setSheet = (i: number, patch: Partial<ExcelSheet>) =>
    setSheets((all) => all.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const setColumn = (i: number, k: number, patch: Partial<ExcelColumn>) =>
    setSheets((all) =>
      all.map((s, j) =>
        j === i ? { ...s, columns: s.columns.map((c, m) => (m === k ? { ...c, ...patch } : c)) } : s,
      ),
    )

  const references = sheets.filter((s) => s.role === 'reference')
  const lookupOptions = references.flatMap((r) =>
    r.columns.map((c) => ({ value: `${r.name}::${c.header}`, label: `${r.name} · ${c.header}` })),
  )
  const matchOptions = sheets
    .filter((s) => ['document', 'items', 'entity'].includes(s.role) && !s.same_as)
    .flatMap((s) =>
      s.columns
        .filter((c) => makesField(c.source))
        .map((c) => ({ value: `${s.name}::${c.header}`, label: `${s.name} · ${c.header}` })),
    )

  const problem = findProblem(title, sheets)
  const build = useMutation({
    mutationFn: () =>
      buildFromExcel(uploadId, { title, category, description, starter: analysis.starter, sheets }),
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t('已建好模板「{{title}}」。导出时会按「{{filename}}」的样子填', { title: tpl.title, filename: analysis.filename }))
      void navigate(`/templates/${tpl.id}`)
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <Flex vertical gap={16}>
      <Card
        title={
          <Flex gap={8} align="center" wrap>
            <Link to="/templates">
              <Button icon={<ArrowLeftOutlined />}>{t('模板')}</Button>
            </Link>
            <Typography.Text strong>{t('从 Excel 建模板：{{filename}}', { filename: analysis.filename })}</Typography.Text>
          </Flex>
        }
        extra={
          <Tooltip title={problem}>
            <Button
              type="primary"
              icon={<CheckOutlined />}
              disabled={problem !== null}
              loading={build.isPending}
              onClick={() => build.mutate()}
            >
              {t('建模板')}
            </Button>
          </Tooltip>
        }
      >
        <Flex vertical gap={12}>
          <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
            {t('系统先猜了每张 sheet 是什么、每一列从哪来，你看一眼，不对的改掉。建好之后，导出时数据按这里定的 填回这个 Excel：单据表一份单据一行，明细表一项一行，对象表同一个编号合并成一行，参照表、选项、说明不动。')}
          </Typography.Paragraph>
          {analysis.starter_title && (
            <Alert
              type="info"
              showIcon
              title={t('表头和起步模板「{{starter_title}}」对上了不少', { starter_title: analysis.starter_title })}
              description={t('对上的列已经用了它的字段 ID、类型和给模型的说明；它的规则里字段都有的，也会一起带过来。')}
            />
          )}
          {problem && <Alert type="warning" showIcon title={problem} />}
          <Flex gap={8} wrap>
            <Input
              addonBefore={t('模板名')}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              style={{ width: 320 }}
            />
            <AutoComplete
              value={category}
              options={CATEGORIES.map((c) => ({ value: c }))}
              onChange={setCategory}
              style={{ width: 140 }}
            />
            <Input
              placeholder={t('说明（可以不写）')}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              style={{ flex: 1, minWidth: 220 }}
            />
          </Flex>
        </Flex>
      </Card>

      {sheets.map((sheet, i) => (
        <SheetCard
          key={sheet.name}
          sheet={sheet}
          autoColumns={analysis.auto_columns}
          lookupOptions={lookupOptions}
          matchOptions={matchOptions}
          onSheet={(patch) => setSheet(i, patch)}
          onColumn={(k, patch) => setColumn(i, k, patch)}
        />
      ))}
    </Flex>
  )
}

/**
 * Checks before creating the template: template name, field IDs, subject identifier columns, reference
 * matching. Returns the first problem.
 */
function findProblem(title: string, sheets: ExcelSheet[]): string | null {
  if (!title.trim()) return t('请给模板起个名字')
  const active = sheets.filter((s) => ['document', 'items', 'entity'].includes(s.role) && !s.same_as)
  const hasField = active.some((s) =>
    s.columns.some((c) => makesField(c.source) && (s.role !== 'items' || c.source === 'doc')),
  )
  if (!hasField) {
    return t('至少要有一张单据表或对象表（或者在明细表里标一列"单据上的字段"）')
  }
  for (const s of active) {
    const local = new Set<string>()
    for (const c of s.columns.filter((c) => makesField(c.source))) {
      if (!FIELD_ID.test(c.field)) return t('「{{name}}」的「{{header}}」字段 ID 不对：小写英文字母开头，只能有小写字母、数字、下划线', { name: s.name, header: c.header })
      if (!c.label.trim()) return t('「{{name}}」的「{{header}}」没有中文名', { name: s.name, header: c.header })
      if (s.role === 'items' && c.source !== 'doc') {
        if (local.has(c.field)) return t('「{{name}}」里列 ID {{field}} 重复了', { name: s.name, field: c.field })
        local.add(c.field)
      }
    }
    if (s.role === 'entity' && !s.columns.some((c) => c.header === s.key_header && makesField(c.source))) {
      return t('对象表「{{name}}」要选一列当编号', { name: s.name })
    }
    for (const c of s.columns) {
      if (c.source === 'auto' && !c.auto) return t('「{{name}}」的「{{header}}」要选自动填什么', { name: s.name, header: c.header })
      if (c.source === 'lookup' && !c.lookup) return t('「{{name}}」的「{{header}}」要选从参照表的哪一列对照', { name: s.name, header: c.header })
    }
  }
  for (const s of sheets.filter((s) => s.role === 'reference')) {
    if (s.match_field && !s.key_header) return t('参照表「{{name}}」要选拿哪一列对照', { name: s.name })
  }
  return null
}

function SheetCard({
  sheet,
  autoColumns,
  lookupOptions,
  matchOptions,
  onSheet,
  onColumn,
}: {
  sheet: ExcelSheet
  autoColumns: Record<string, string>
  lookupOptions: { value: string; label: string }[]
  matchOptions: { value: string; label: string }[]
  onSheet: (patch: Partial<ExcelSheet>) => void
  onColumn: (index: number, patch: Partial<ExcelColumn>) => void
}) {
  const { t } = useTranslation()
  const role = ROLE_TEXT[sheet.role]
  const roleSelect = (
    <Select<SheetRole>
      value={sheet.role}
      onChange={(r) => onSheet({ role: r })}
      style={{ width: 150 }}
      options={(Object.keys(ROLE_TEXT) as SheetRole[]).map((r) => ({
        value: r,
        label: ROLE_TEXT[r].label,
        disabled: sheet.header_row === 0 && !['options', 'ignore'].includes(r),
      }))}
    />
  )
  const head = (
    <Flex gap={8} align="center" wrap>
      <Typography.Text strong>{sheet.name}</Typography.Text>
      <Tag color={role.color}>{role.label}</Tag>
      {sheet.hidden && <Tag>{t('隐藏')}</Tag>}
      {sheet.header_row > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('表头在第 {{header_row}} 行 · {{rows}} 行数据', { header_row: sheet.header_row, rows: sheet.rows })}
        </Typography.Text>
      )}
    </Flex>
  )
  const why = (
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
      {role.help}
      {sheet.reason ? t('（{{reason}}）', { reason: sheet.reason }) : ''}
    </Typography.Text>
  )

  if (sheet.same_as) {
    return (
      <Card size="small" title={head} extra={roleSelect}>
        <Flex vertical gap={4}>
          {why}
          <Typography.Text>
            {t('和「{{same_as}}」表头一样（像按月分的），按它来。导出时原来的几张都不动，复制一张新的填进去。', { same_as: sheet.same_as })}
          </Typography.Text>
          <Button size="small" style={{ alignSelf: 'start' }} onClick={() => onSheet({ same_as: '' })}>
            {t('不一样，单独设置')}
          </Button>
        </Flex>
      </Card>
    )
  }

  if (sheet.role === 'reference') {
    return (
      <Card size="small" title={head} extra={roleSelect}>
        <Flex vertical gap={8}>
          {why}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('列：{{columns}}。整张表存进模板，以后要更新就重新从 Excel 建。', { columns: sheet.columns.map((c) => c.header).join(t('、')) })}
          </Typography.Text>
          <Flex gap={8} align="center" wrap>
            <Typography.Text>{t('拿单据上的')}</Typography.Text>
            <Select
              allowClear
              placeholder={t('不对照')}
              style={{ minWidth: 220 }}
              value={sheet.match_field || undefined}
              onChange={(v: string | undefined) => onSheet({ match_field: v ?? '' })}
              options={matchOptions}
            />
            <Typography.Text>{t('到这张表的')}</Typography.Text>
            <Select
              style={{ minWidth: 140 }}
              value={sheet.key_header || undefined}
              placeholder={t('哪一列')}
              disabled={!sheet.match_field}
              onChange={(v: string) => onSheet({ key_header: v })}
              options={sheet.columns.map((c) => ({ value: c.header, label: c.header }))}
            />
            <Typography.Text>{t('里找；找不到会提醒')}</Typography.Text>
          </Flex>
        </Flex>
      </Card>
    )
  }

  if (!['document', 'items', 'entity'].includes(sheet.role)) {
    return (
      <Card size="small" title={head} extra={roleSelect}>
        {why}
      </Card>
    )
  }

  const sources = SOURCES[sheet.role]
  const columns: TableProps<ExcelColumn>['columns'] = [
    {
      title: t('表头'),
      dataIndex: 'header',
      width: 150,
      render: (h: string, c) => (
        <Flex vertical>
          <Typography.Text>{h}</Typography.Text>
          <Typography.Text type="secondary" ellipsis style={{ fontSize: 12, maxWidth: 140 }}>
            {c.samples.slice(0, 2).join(t('、')) || t('（没有示例）')}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('从哪来'),
      dataIndex: 'source',
      width: 210,
      render: (source: ColumnSource, c, k) => (
        <Flex vertical gap={4}>
          <Select<ColumnSource>
            value={source}
            onChange={(v) => onColumn(k, { source: v })}
            options={sources.map((s) => ({ value: s, label: SOURCE_TEXT[s] }))}
            style={{ width: '100%' }}
          />
          {source === 'auto' && (
            <Select
              value={c.auto || undefined}
              placeholder={t('自动填什么')}
              onChange={(v: string) => onColumn(k, { auto: v })}
              options={Object.entries(autoColumns).map(([value, label]) => ({ value, label }))}
              style={{ width: '100%' }}
            />
          )}
          {source === 'lookup' && (
            <Select
              value={c.lookup || undefined}
              placeholder={lookupOptions.length ? t('参照表的哪一列') : t('先把一张 sheet 设成参照表')}
              onChange={(v: string) => onColumn(k, { lookup: v })}
              options={lookupOptions}
              style={{ width: '100%' }}
            />
          )}
        </Flex>
      ),
    },
    {
      title: t('中文名'),
      dataIndex: 'label',
      width: 140,
      render: (label: string, c, k) =>
        makesField(c.source) ? (
          <Input value={label} onChange={(e) => onColumn(k, { label: e.target.value })} />
        ) : null,
    },
    {
      title: (
        <Tooltip title={t('小写英文字母开头。几张 sheet 里同一个字段 ID 就是同一个字段（只抽一次）')}>
          {t('字段 ID')}
        </Tooltip>
      ),
      dataIndex: 'field',
      width: 150,
      render: (field: string, c, k) =>
        makesField(c.source) ? (
          <Input
            value={field}
            status={FIELD_ID.test(field) ? undefined : 'error'}
            onChange={(e) => onColumn(k, { field: e.target.value.trim() })}
          />
        ) : null,
    },
    {
      title: t('类型'),
      dataIndex: 'type',
      width: 130,
      render: (type: ExcelColumn['type'], c, k) =>
        makesField(c.source) ? (
          <Select
            value={type}
            options={TYPE_OPTIONS}
            onChange={(v) => onColumn(k, { type: v })}
            style={{ width: '100%' }}
          />
        ) : null,
    },
    {
      title: t('必填'),
      dataIndex: 'required',
      width: 60,
      render: (on: boolean, c, k) =>
        makesField(c.source) ? (
          <Switch size="small" checked={on} onChange={(v) => onColumn(k, { required: v })} />
        ) : null,
    },
    {
      title: t('敏感'),
      dataIndex: 'sensitive',
      width: 60,
      render: (on: boolean, c, k) =>
        makesField(c.source) ? (
          <Switch size="small" checked={on} onChange={(v) => onColumn(k, { sensitive: v })} />
        ) : null,
    },
    {
      title: t('可选值'),
      dataIndex: 'options',
      render: (options: string[]) =>
        options.length ? (
          <Typography.Text type="secondary" ellipsis style={{ fontSize: 12, maxWidth: 180 }}>
            {options.join(t('、'))}
          </Typography.Text>
        ) : null,
    },
  ]

  const keyChoices = sheet.columns
    .filter((c) => makesField(c.source))
    .map((c) => ({ value: c.header, label: c.header }))

  return (
    <Card size="small" title={head} extra={roleSelect}>
      <Flex vertical gap={8}>
        {why}
        {sheet.role === 'entity' && (
          <Space wrap>
            <Typography.Text>{t('编号列（编号相同就是同一个）：')}</Typography.Text>
            <Select
              value={sheet.key_header || undefined}
              placeholder={t('选一列')}
              status={sheet.key_header ? undefined : 'error'}
              onChange={(v: string) => onSheet({ key_header: v })}
              options={keyChoices}
              style={{ minWidth: 160 }}
            />
          </Space>
        )}
        {sheet.role === 'items' && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('明细表里"单据上的字段（每行重复）"的列，就是连到单据的列（单号、病人编号……），导出时每一行都填上。')}
          </Typography.Text>
        )}
        <Table<ExcelColumn>
          size="small"
          rowKey="index"
          columns={columns}
          dataSource={sheet.columns}
          pagination={false}
          scroll={{ x: 1100 }}
        />
      </Flex>
    </Card>
  )
}
