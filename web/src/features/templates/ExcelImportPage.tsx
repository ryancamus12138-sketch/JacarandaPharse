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
        title={t('无法打开此 Excel 文件')}
        subTitle={text}
        extra={
          <Link to="/templates">
            <Button>{t('返回模板列表')}</Button>
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
      void message.success(t('已创建模板「{{title}}」。导出时将按「{{filename}}」的版式填写', { title: tpl.title, filename: analysis.filename }))
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
            <Typography.Text strong>{t('从 Excel 创建模板：{{filename}}', { filename: analysis.filename })}</Typography.Text>
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
              {t('创建模板')}
            </Button>
          </Tooltip>
        }
      >
        <Flex vertical gap={12}>
          <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
            {t('系统已推测每张 sheet 的类型及每列的数据来源，请核对并修正。创建后，导出时数据将按此处设置回填至该 Excel：单据表每份单据一行，明细表每项一行，对象表按编号合并为一行，参照表、选项和说明保持不变。')}
          </Typography.Paragraph>
          {analysis.starter_title && (
            <Alert
              type="info"
              showIcon
              title={t('表头与起步模板「{{starter_title}}」大量匹配', { starter_title: analysis.starter_title })}
              description={t('匹配的列已采用其字段 ID、类型和给模型的说明；所需字段齐全的规则也将一并导入。')}
            />
          )}
          {problem && <Alert type="warning" showIcon title={problem} />}
          <Flex gap={8} wrap>
            <Input
              addonBefore={t('模板名称')}
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
              placeholder={t('说明（选填）')}
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
  if (!title.trim()) return t('请输入模板名称')
  const active = sheets.filter((s) => ['document', 'items', 'entity'].includes(s.role) && !s.same_as)
  const hasField = active.some((s) =>
    s.columns.some((c) => makesField(c.source) && (s.role !== 'items' || c.source === 'doc')),
  )
  if (!hasField) {
    return t('至少需要一张单据表或对象表（或在明细表中将一列设为“单据级字段”）')
  }
  for (const s of active) {
    const local = new Set<string>()
    for (const c of s.columns.filter((c) => makesField(c.source))) {
      if (!FIELD_ID.test(c.field)) return t('「{{name}}」的「{{header}}」字段 ID 无效：需以小写英文字母开头，且只能包含小写字母、数字和下划线', { name: s.name, header: c.header })
      if (!c.label.trim()) return t('「{{name}}」的「{{header}}」未填写名称', { name: s.name, header: c.header })
      if (s.role === 'items' && c.source !== 'doc') {
        if (local.has(c.field)) return t('「{{name}}」中列 ID {{field}} 重复', { name: s.name, field: c.field })
        local.add(c.field)
      }
    }
    if (s.role === 'entity' && !s.columns.some((c) => c.header === s.key_header && makesField(c.source))) {
      return t('对象表「{{name}}」需选择一列作为编号', { name: s.name })
    }
    for (const c of s.columns) {
      if (c.source === 'auto' && !c.auto) return t('「{{name}}」的「{{header}}」需选择自动填充内容', { name: s.name, header: c.header })
      if (c.source === 'lookup' && !c.lookup) return t('「{{name}}」的「{{header}}」需选择参照表中的对照列', { name: s.name, header: c.header })
    }
  }
  for (const s of sheets.filter((s) => s.role === 'reference')) {
    if (s.match_field && !s.key_header) return t('参照表「{{name}}」需选择对照列', { name: s.name })
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
            {t('表头与「{{same_as}}」相同（如按月分表），沿用其设置。导出时原有 sheet 保持不变，复制一张新 sheet 填写。', { same_as: sheet.same_as })}
          </Typography.Text>
          <Button size="small" style={{ alignSelf: 'start' }} onClick={() => onSheet({ same_as: '' })}>
            {t('不同，单独设置')}
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
            {t('列：{{columns}}。整张表将保存在模板中；如需更新，请重新从 Excel 创建。', { columns: sheet.columns.map((c) => c.header).join(t('、')) })}
          </Typography.Text>
          <Flex gap={8} align="center" wrap>
            <Typography.Text>{t('用单据中的')}</Typography.Text>
            <Select
              allowClear
              placeholder={t('不对照')}
              style={{ minWidth: 220 }}
              value={sheet.match_field || undefined}
              onChange={(v: string | undefined) => onSheet({ match_field: v ?? '' })}
              options={matchOptions}
            />
            <Typography.Text>{t('在本表的')}</Typography.Text>
            <Select
              style={{ minWidth: 140 }}
              value={sheet.key_header || undefined}
              placeholder={t('对照列')}
              disabled={!sheet.match_field}
              onChange={(v: string) => onSheet({ key_header: v })}
              options={sheet.columns.map((c) => ({ value: c.header, label: c.header }))}
            />
            <Typography.Text>{t('中查找；未找到时提示')}</Typography.Text>
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
            {c.samples.slice(0, 2).join(t('、')) || t('（无示例）')}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('数据来源'),
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
              placeholder={t('自动填充')}
              onChange={(v: string) => onColumn(k, { auto: v })}
              options={Object.entries(autoColumns).map(([value, label]) => ({ value, label }))}
              style={{ width: '100%' }}
            />
          )}
          {source === 'lookup' && (
            <Select
              value={c.lookup || undefined}
              placeholder={lookupOptions.length ? t('参照表对照列') : t('请先将一张 sheet 设为参照表')}
              onChange={(v: string) => onColumn(k, { lookup: v })}
              options={lookupOptions}
              style={{ width: '100%' }}
            />
          )}
        </Flex>
      ),
    },
    {
      title: t('名称'),
      dataIndex: 'label',
      width: 140,
      render: (label: string, c, k) =>
        makesField(c.source) ? (
          <Input value={label} onChange={(e) => onColumn(k, { label: e.target.value })} />
        ) : null,
    },
    {
      title: (
        <Tooltip title={t('需以小写英文字母开头。多张 sheet 中相同的字段 ID 视为同一字段（仅抽取一次）')}>
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
            <Typography.Text>{t('编号列（编号相同视为同一对象）：')}</Typography.Text>
            <Select
              value={sheet.key_header || undefined}
              placeholder={t('选择列')}
              status={sheet.key_header ? undefined : 'error'}
              onChange={(v: string) => onSheet({ key_header: v })}
              options={keyChoices}
              style={{ minWidth: 160 }}
            />
          </Space>
        )}
        {sheet.role === 'items' && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('明细表中设为“单据级字段（每行重复）”的列即关联单据的列（如单号、病人编号），导出时每行均会填写。')}
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
