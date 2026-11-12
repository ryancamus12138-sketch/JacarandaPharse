import { ArrowLeftOutlined, DeleteOutlined, ExperimentOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Flex,
  Input,
  List,
  Modal,
  Result,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { listDocuments } from '../../api/documents'
import {
  createTemplate,
  getTemplate,
  getTemplateVersion,
  listRules,
  saveTemplate,
  trialTemplate,
} from '../../api/templates'
import type { RuleInfo, Template, TemplateIn, TemplateTable, TrialResult } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { FieldsEditor } from './FieldsEditor'
import { StructureCard } from './StructureCard'
import { blankField, CATEGORIES, FIELD_ID, fieldsProblem } from './templateFields'


const STATUS_TEXT: Record<string, [string, string]> = {
  matched: ['有出处', 'green'],
  fuzzy: ['相似', 'gold'],
  not_found: ['找不到出处', 'red'],
  empty: ['原文没有', 'default'],
}

function draftOf(t: Template): TemplateIn {
  return {
    title: t.title,
    category: t.category,
    description: t.description,
    fields: t.fields,
    tables: t.tables,
    rules: t.rules.map((r) => r.id),
    note: '',
  }
}

/** 规则要用的字段这个模板里有没有：没有的话这条规则永远跳过。 */
function missingFor(rule: RuleInfo, draft: TemplateIn): string[] {
  const names = new Set(draft.fields.map((f) => f.name))
  for (const t of draft.tables) {
    names.add(t.name)
    for (const c of t.columns) names.add(`${t.name}.*.${c.name}`)
  }
  if (rule.per_row) {
    const table = draft.tables.find((t) => t.name === rule.per_row)
    if (!table) return [rule.per_row]
    return rule.needs.filter((n) => !table.columns.some((c) => c.name === n))
  }
  return rule.needs.filter((n) => !names.has(n))
}

/**
 * 编辑我的模板：字段、表格、规则；每次保存是一个新版本（旧文档还按原来的版本）。
 * 也可以先"试跑"：用最新版在一份已处理的文档上抽一次看看，不保存。
 */
/** 新建空白模板：还没存进数据库，点了保存才建（第 1 版）。 */
const BLANK: Template = {
  name: '',
  version: 0,
  title: '新模板',
  description: '',
  category: '',
  kind: 'mine',
  id: null,
  archived: false,
  source: 'blank',
  updated_at: null,
  fields: [
    {
      name: 'total',
      label: '总额',
      type: 'number',
      required: true,
      description: '',
      aliases: [],
      export: true,
      sensitive: false,
      options: [],
      extract: true,
    },
  ],
  tables: [],
  rules: [],
  versions: [],
  used: 0,
}

export function TemplateEditorPage() {
  const { id = '' } = useParams()
  const isNew = id === 'new'
  const template = useQuery({
    queryKey: ['template', id],
    queryFn: () => getTemplate(id),
    enabled: !isNew,
  })
  if (isNew) {
    return <Editor key="new" template={BLANK} />
  }
  if (template.isPending) {
    return (
      <Flex justify="center" style={{ padding: 96 }}>
        <Spin size="large" />
      </Flex>
    )
  }
  if (template.error) {
    const text = template.error instanceof ApiError ? template.error.message : String(template.error)
    return (
      <Result
        status="warning"
        title="打不开这个模板"
        subTitle={text}
        extra={
          <Link to="/templates">
            <Button>回模板列表</Button>
          </Link>
        }
      />
    )
  }
  // key：保存出新版本后，编辑区从新版本重新开始
  return <Editor key={`${id}:${template.data.version}`} template={template.data} />
}

function Editor({ template }: { template: Template }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const readOnly = template.kind !== 'mine' || !can('template:manage')
  const [draft, setDraft] = useState<TemplateIn>(() => draftOf(template))
  const [viewing, setViewing] = useState<number | null>(null)
  const [trialDoc, setTrialDoc] = useState<string>()
  const set = (patch: Partial<TemplateIn>) => setDraft((d) => ({ ...d, ...patch }))
  // 已经存过的字段 ID 不能改（新建的还没存过，都能改）
  const saved = template.id !== null
  const locked = useMemo(
    () => new Set(saved ? template.fields.map((f) => f.name) : []),
    [template, saved],
  )
  const lockedTables = useMemo(
    () => new Set(saved ? template.tables.map((t) => t.name) : []),
    [template, saved],
  )

  const rules = useQuery({ queryKey: ['template-rules'], queryFn: listRules, staleTime: Infinity })
  const oldVersion = useQuery({
    queryKey: ['template', template.id, 'version', viewing],
    queryFn: () => getTemplateVersion(template.id ?? '', viewing ?? 1),
    enabled: viewing !== null,
  })
  const documents = useQuery({
    queryKey: ['documents', 'trial'],
    queryFn: () => listDocuments(50, 0, { status: ['done', 'reviewed'] }),
    enabled: !readOnly,
  })
  const onError = (err: unknown) =>
    void message.error(err instanceof ApiError ? err.message : String(err))

  const isNew = template.id === null
  const navigate = useNavigate()
  // 新建的：点了保存才建，建好换到它自己的页面
  const create = useMutation({
    mutationFn: () => createTemplate({ source: 'blank', content: draft }),
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(`已保存「${t.title}」（第 1 版）`)
      void navigate(`/templates/${t.id}`, { replace: true })
    },
    onError: (err: unknown) =>
      void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const save = useMutation({
    mutationFn: () => saveTemplate(template.id ?? '', draft),
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      queryClient.setQueryData(['template', t.id], { ...template, ...t, versions: template.versions })
      void queryClient.invalidateQueries({ queryKey: ['template', t.id] })
      void message.success(`已保存第 ${t.version} 版。以后上传、重新处理用这一版，旧文档不变`)
    },
    onError,
  })
  const trial = useMutation({
    mutationFn: (documentId: string) => trialTemplate(template.id ?? '', documentId),
    onError,
  })

  const dirty = JSON.stringify({ ...draft, note: '' }) !== JSON.stringify(draftOf(template))
  const problem =
    (!draft.title.trim() ? '模板名不能空' : null) ??
    (draft.fields.length === 0 ? '至少要有一个字段' : null) ??
    fieldsProblem(draft.fields, '字段里') ??
    draft.tables
      .map(
        (t) =>
          (!FIELD_ID.test(t.name) ? `表格 ID「${t.name}」只能用小写英文字母、数字和下划线` : null) ??
          (t.columns.length === 0 ? `表格「${t.label}」至少要有一列` : null) ??
          fieldsProblem(t.columns, `表格「${t.label}」`),
      )
      .find((p) => p !== null) ??
    null

  const setTable = (i: number, patch: Partial<TemplateTable>) =>
    set({ tables: draft.tables.map((t, j) => (j === i ? { ...t, ...patch } : t)) })

  // 选了的在前，其次是这个模板能用的（字段都有），最后是缺字段的
  const ruleGroups = (rules.data ?? [])
    .map((rule) => ({
      rule,
      missing: missingFor(rule, draft),
      on: template.rules.some((r) => r.id === rule.id),
    }))
    .sort((a, b) => Number(b.on) - Number(a.on) || a.missing.length - b.missing.length)
    .map((g) => ({ ...g, on: draft.rules.includes(g.rule.id) }))

  return (
    <Flex vertical gap={16}>
      <Card
        title={
          <Flex gap={8} align="center" wrap>
            <Link to="/templates">
              <Button icon={<ArrowLeftOutlined />}>模板</Button>
            </Link>
            <Typography.Text strong>{isNew ? '新建模板' : template.title}</Typography.Text>
            {isNew ? <Tag color="orange">还没保存</Tag> : <Tag>第 {template.version} 版</Tag>}
            {template.archived && <Tag>已归档</Tag>}
            {template.kind !== 'mine' && <Tag color="blue">只读（{template.kind === 'starter' ? '起步模板' : '内置模板'}）</Tag>}
          </Flex>
        }
        extra={
          !readOnly && (
            isNew ? (
              <Space>
                <Link to="/templates">
                  <Button>取消</Button>
                </Link>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  disabled={problem !== null}
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  保存
                </Button>
              </Space>
            ) : (
              <Space.Compact>
                <Input
                  placeholder="这一版改了什么（可以不写）"
                  value={draft.note}
                  onChange={(e) => set({ note: e.target.value })}
                  style={{ width: 260 }}
                />
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  disabled={!dirty || problem !== null}
                  loading={save.isPending}
                  onClick={() => save.mutate()}
                >
                  保存为第 {template.version + 1} 版
                </Button>
              </Space.Compact>
            )
          )
        }
      >
        {problem && (dirty || isNew) && (
          <Alert type="error" showIcon title={problem} style={{ marginBottom: 12 }} />
        )}
        {isNew && !problem && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="填好字段后点右上角“保存”，才会加到我的模板里"
            description="离开这个页面不保存，什么都不会留下。"
          />
        )}
        {template.used > 0 && !readOnly && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title={`有 ${template.used} 份文档用这个模板`}
            description="保存新版本不会改它们已有的结果；想按新版本重新抽，在审核页面点“重新处理”。"
          />
        )}
        <Descriptions column={{ xs: 1, md: 3 }} size="small">
          <Descriptions.Item label="模板名">
            <Input value={draft.title} disabled={readOnly} onChange={(e) => set({ title: e.target.value })} />
          </Descriptions.Item>
          <Descriptions.Item label="类型">
            <AutoComplete
              value={draft.category}
              disabled={readOnly}
              options={CATEGORIES.map((c) => ({ value: c }))}
              onChange={(category: string) => set({ category })}
              style={{ width: '100%' }}
            />
          </Descriptions.Item>
          <Descriptions.Item label="说明">
            <Input
              value={draft.description}
              disabled={readOnly}
              onChange={(e) => set({ description: e.target.value })}
            />
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title={`字段（${draft.fields.length}）`} size="small">
        <FieldsEditor
          fields={draft.fields}
          onChange={(fields) => set({ fields })}
          locked={locked}
          readOnly={readOnly}
        />
      </Card>

      <Card
        title={`表格（${draft.tables.length}）`}
        size="small"
        extra={
          !readOnly && (
            <Button
              icon={<PlusOutlined />}
              onClick={() =>
                set({
                  tables: [
                    ...draft.tables,
                    {
                      name: draft.tables.some((t) => t.name === 'items') ? `table_${draft.tables.length + 1}` : 'items',
                      label: '明细',
                      description: '',
                      required: false,
                      export: true,
                      columns: [{ ...blankField([]), name: 'amount', label: '金额', type: 'number' }],
                    },
                  ],
                })
              }
            >
              加一张表
            </Button>
          )
        }
      >
        {draft.tables.length === 0 ? (
          <Empty description="没有表格。商品明细、航段这种行数不固定的，用表格" />
        ) : (
          <Flex vertical gap={16}>
            {draft.tables.map((t, i) => (
              <Card
                key={i}
                type="inner"
                size="small"
                title={
                  <Flex gap={8} align="center" wrap>
                    <Input
                      value={t.label}
                      disabled={readOnly}
                      onChange={(e) => setTable(i, { label: e.target.value })}
                      style={{ width: 140 }}
                      addonBefore="中文名"
                    />
                    <Input
                      value={t.name}
                      disabled={readOnly || lockedTables.has(t.name)}
                      status={FIELD_ID.test(t.name) ? undefined : 'error'}
                      onChange={(e) => setTable(i, { name: e.target.value.trim() })}
                      style={{ width: 160 }}
                      addonBefore="ID"
                    />
                    <Checkbox checked={t.required} disabled={readOnly} onChange={(e) => setTable(i, { required: e.target.checked })}>
                      至少一行
                    </Checkbox>
                    <Checkbox checked={t.export} disabled={readOnly} onChange={(e) => setTable(i, { export: e.target.checked })}>
                      导出
                    </Checkbox>
                  </Flex>
                }
                extra={
                  !readOnly && (
                    <Button
                      danger
                      type="text"
                      icon={<DeleteOutlined />}
                      onClick={() => set({ tables: draft.tables.filter((_, j) => j !== i) })}
                    >
                      删掉这张表
                    </Button>
                  )
                }
              >
                <Input.TextArea
                  value={t.description}
                  disabled={readOnly}
                  autoSize={{ minRows: 1, maxRows: 3 }}
                  placeholder="给模型的说明：表在单据哪里、哪些行不算（小计、合计……）"
                  onChange={(e) => setTable(i, { description: e.target.value })}
                  style={{ marginBottom: 8 }}
                />
                <FieldsEditor
                  fields={t.columns}
                  onChange={(columns) => setTable(i, { columns })}
                  locked={
                    new Set(
                      saved
                        ? (template.tables.find((x) => x.name === t.name)?.columns.map((c) => c.name) ?? [])
                        : [],
                    )
                  }
                  readOnly={readOnly}
                />
              </Card>
            ))}
          </Flex>
        )}
      </Card>

      <Card title={`规则（选了 ${draft.rules.length} 条）`} size="small">
        <Typography.Paragraph type="secondary">
          规则用单据上的数字互相验证（例如 合计金额 + 合计税额 = 价税合计）。规则按字段 ID 找值，
          模板里缺它要的字段时，这条规则会自动跳过。必填字段没值也会标红。
        </Typography.Paragraph>
        <List
          size="small"
          loading={rules.isPending}
          dataSource={ruleGroups.filter((g) => !readOnly || g.on)}
          renderItem={({ rule, missing, on }) => (
            <List.Item>
              <Flex gap={8} align="center" wrap style={{ width: '100%' }}>
                <Switch
                  size="small"
                  checked={on}
                  disabled={readOnly}
                  onChange={(checked) =>
                    set({
                      rules: checked ? [...draft.rules, rule.id] : draft.rules.filter((r) => r !== rule.id),
                    })
                  }
                />
                <Typography.Text>{rule.title}</Typography.Text>
                <Tag color={rule.severity === 'error' ? 'red' : 'gold'}>
                  {rule.severity === 'error' ? '错误' : '警告'}
                </Tag>
                {on && missing.length > 0 && (
                  <Typography.Text type="warning" style={{ fontSize: 12 }}>
                    缺字段 {missing.join('、')}，会跳过
                  </Typography.Text>
                )}
              </Flex>
            </List.Item>
          )}
        />
      </Card>

      {(template.workbook || (template.entities ?? []).length > 0 || (template.references ?? []).length > 0) && (
        <StructureCard template={template} />
      )}

      {!readOnly && !isNew && (
        <Card
          size="small"
          title={
            <Space>
              <ExperimentOutlined />
              试跑
            </Space>
          }
        >
          <Typography.Paragraph type="secondary">
            用<b>已保存的最新版</b>（第 {template.version} 版）在一份处理过的文档上抽一次，看看抽出什么。
            不保存、不改文档。要等模型跑完，每页十几秒。
          </Typography.Paragraph>
          <Flex gap={8} wrap style={{ marginBottom: 12 }}>
            <Select
              style={{ minWidth: 320 }}
              placeholder="选一份文档"
              value={trialDoc}
              onChange={setTrialDoc}
              loading={documents.isPending}
              showSearch={{ optionFilterProp: 'label' }}
              options={documents.data?.items.map((d) => ({ value: d.id, label: d.filename }))}
            />
            <Button
              icon={<ExperimentOutlined />}
              disabled={!trialDoc}
              loading={trial.isPending}
              onClick={() => trialDoc && trial.mutate(trialDoc)}
            >
              试跑
            </Button>
            {dirty && <Typography.Text type="warning">有没保存的修改：试跑用的是已保存的版本</Typography.Text>}
          </Flex>
          {trial.data && <TrialView result={trial.data} />}
        </Card>
      )}

      {template.kind === 'mine' && !isNew && (
        <Card size="small" title="历史版本">
          <List
            size="small"
            dataSource={template.versions}
            renderItem={(v) => (
              <List.Item
                actions={[
                  <Button key="view" type="link" onClick={() => setViewing(v.version)}>
                    查看
                  </Button>,
                ]}
              >
                <Space>
                  <Tag color={v.version === template.version ? 'purple' : undefined}>第 {v.version} 版</Tag>
                  <Typography.Text type="secondary">{formatTime(v.created_at)}</Typography.Text>
                  <Typography.Text>{v.note}</Typography.Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}

      <Modal
        open={viewing !== null}
        title={`第 ${viewing ?? ''} 版（只读）`}
        footer={null}
        width={720}
        onCancel={() => setViewing(null)}
      >
        {oldVersion.data ? (
          <Table
            size="small"
            rowKey="name"
            pagination={false}
            dataSource={[
              ...oldVersion.data.fields,
              ...oldVersion.data.tables.flatMap((t) =>
                t.columns.map((c) => ({ ...c, name: `${t.name}.${c.name}`, label: `${t.label} · ${c.label}` })),
              ),
            ]}
            columns={[
              { title: '中文名', dataIndex: 'label' },
              { title: '字段 ID', dataIndex: 'name' },
              { title: '类型', dataIndex: 'type' },
              { title: '必填', dataIndex: 'required', render: (r: boolean) => (r ? '是' : '') },
            ]}
          />
        ) : (
          <Spin />
        )}
      </Modal>
    </Flex>
  )
}

function TrialView({ result }: { result: TrialResult }) {
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        第 {result.version} 版，用时 {(result.duration_ms / 1000).toFixed(1)} 秒
      </Typography.Text>
      {result.pages.map((page) => (
        <Flex key={page.page} vertical gap={8}>
          {result.pages.length > 1 && <Typography.Text strong>第 {page.page} 页</Typography.Text>}
          {page.issues.map((issue, i) => (
            <Alert
              key={`${issue.rule}:${i}`}
              type={issue.severity === 'error' ? 'error' : 'warning'}
              showIcon
              title={issue.title}
              description={issue.message}
            />
          ))}
          <Table
            size="small"
            rowKey="name"
            pagination={false}
            dataSource={page.fields}
            columns={[
              { title: '字段', dataIndex: 'label', width: 200 },
              { title: '抽出的值', dataIndex: 'value', render: (v: string | null) => v ?? <Typography.Text type="secondary">（空）</Typography.Text> },
              {
                title: '出处',
                dataIndex: 'status',
                width: 120,
                render: (s: string) => <Tag color={STATUS_TEXT[s]?.[1]}>{STATUS_TEXT[s]?.[0] ?? s}</Tag>,
              },
            ]}
          />
        </Flex>
      ))}
    </Flex>
  )
}
