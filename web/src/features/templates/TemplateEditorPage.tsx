import { ArrowLeftOutlined, DeleteOutlined, ExperimentOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
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
  Table,
  Tag,
  Tooltip,
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
import type { Template, TemplateIn, TemplateTable, TrialResult } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { LANGUAGE_NAMES, languageName, languageOptions } from '../../utils/languages'
import { FieldsEditor } from './FieldsEditor'
import { RulesCard } from './RulesCard'
import { StructureCard } from './StructureCard'
import { blankField, FIELD_ID, fieldsProblem } from './templateFields'
import { LabelFields, LabelTags } from './LabelWidgets'
import { Tip } from '../../components/Tip'
import { useTranslation } from 'react-i18next'
import { msg, t } from '../../i18n'
import { DEFAULT_LANGUAGE, newTemplateLanguage, untranslated, withLanguage } from './templateLanguage'


const STATUS_TEXT: Record<string, [string, string]> = {
  matched: [msg('有出处'), 'green'],
  fuzzy: [msg('相似'), 'gold'],
  not_found: [msg('未找到出处'), 'red'],
  empty: [msg('原文缺失'), 'default'],
}

function draftOf(tpl: Template): TemplateIn {
  return {
    title: tpl.title,
    titles: tpl.titles ?? {},
    language: tpl.language ?? DEFAULT_LANGUAGE,
    category: tpl.category,
    description: tpl.description,
    descriptions: tpl.descriptions ?? {},
    fields: tpl.fields,
    tables: tpl.tables,
    rules: tpl.rules.map((r) => r.id),
    custom_rules: tpl.custom_rules ?? [],
    domains: tpl.domains ?? (tpl.category ? [tpl.category] : []),
    countries: tpl.countries ?? [],
    tags: tpl.tags ?? [],
    note: '',
  }
}

/**
 * Edit one of my templates: fields, tables, rules; every save creates a new version (older documents keep
 * their version). A trial run extracts one processed document with the latest version without saving.
 */
/** New blank template: not stored in the database until saved (as version 1). */
const BLANK: Template = {
  name: '',
  version: 0,
  get title() { return t('新模板') },
  get language() { return newTemplateLanguage() },
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
      get label() { return t('总额') },
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
  custom_rules: [],
  domains: [],
  countries: [],
  tags: [],
  versions: [],
  used: 0,
}

export function TemplateEditorPage() {
  const { t } = useTranslation()
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
        title={t('无法打开此模板')}
        subTitle={text}
        extra={
          <Link to="/templates">
            <Button>{t('返回模板列表')}</Button>
          </Link>
        }
      />
    )
  }
  // key: after saving a new version, the editor restarts from it.
  return <Editor key={`${id}:${template.data.version}`} template={template.data} />
}

function Editor({ template }: { template: Template }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const readOnly = template.kind !== 'mine' || !can('template:manage')
  const [draft, setDraft] = useState<TemplateIn>(() => draftOf(template))
  const [viewing, setViewing] = useState<number | null>(null)
  const [trialDoc, setTrialDoc] = useState<string>()
  const set = (patch: Partial<TemplateIn>) => setDraft((d) => ({ ...d, ...patch }))
  // Primary language: title, description and every name and description are written in it; the model gets
  // its descriptions, and the review page and exports show its names by default.
  const primary = draft.language ?? DEFAULT_LANGUAGE
  // Editing language: the primary language first, then languages already in the template, then other common
  // languages that can be added.
  const [language, setLanguage] = useState(primary)
  const editorLanguages = [
    ...new Set([primary, ...(template.languages ?? []), ...Object.keys(LANGUAGE_NAMES)]),
  ]
  const changePrimary = (next: string) => {
    const missing = untranslated(draft, next)
    setDraft(withLanguage(draft, next))
    setLanguage(next)
    if (missing > 0) {
      void message.warning(
        t('主语言已改为{{languageName}}。有 {{count}} 处尚无{{languageName}}版本，暂时保留原文字，请补充翻译', {
          languageName: languageName(next),
          count: missing,
        }),
      )
    }
  }
  // Field IDs already saved cannot change (new, unsaved fields can).
  const saved = template.id !== null
  const locked = useMemo(
    () => new Set(saved ? template.fields.map((f) => f.name) : []),
    [template, saved],
  )
  const lockedTables = useMemo(
    () => new Set(saved ? template.tables.map((tbl) => tbl.name) : []),
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
  // New template: created on save, then navigate to its own page.
  const create = useMutation({
    mutationFn: () => createTemplate({ source: 'blank', content: draft }),
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t('已保存「{{title}}」（第 1 版）', { title: tpl.title }))
      void navigate(`/templates/${tpl.id}`, { replace: true })
    },
    onError: (err: unknown) =>
      void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const save = useMutation({
    mutationFn: () => saveTemplate(template.id ?? '', draft),
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      queryClient.setQueryData(['template', tpl.id], { ...template, ...tpl, versions: template.versions })
      void queryClient.invalidateQueries({ queryKey: ['template', tpl.id] })
      void message.success(t('已保存第 {{version}} 版。之后的上传和重新处理将使用此版本，已有文档不受影响', { version: tpl.version }))
    },
    onError,
  })
  const trial = useMutation({
    mutationFn: (documentId: string) => trialTemplate(template.id ?? '', documentId),
    onError,
  })

  const dirty = JSON.stringify({ ...draft, note: '' }) !== JSON.stringify(draftOf(template))
  const problem =
    (!draft.title.trim() ? t('模板名称不能为空') : null) ??
    (draft.fields.length === 0 ? t('至少需要一个字段') : null) ??
    fieldsProblem(draft.fields, t('字段里')) ??
    draft.tables
      .map(
        (tbl) =>
          (!FIELD_ID.test(tbl.name) ? t('表格 ID「{{name}}」只能包含小写英文字母、数字和下划线', { name: tbl.name }) : null) ??
          (tbl.columns.length === 0 ? t('表格「{{label}}」至少需要一列', { label: tbl.label }) : null) ??
          fieldsProblem(tbl.columns, t('表格「{{label}}」', { label: tbl.label })),
      )
      .find((p) => p !== null) ??
    null

  const setTable = (i: number, patch: Partial<TemplateTable>) =>
    set({ tables: draft.tables.map((tbl, j) => (j === i ? { ...tbl, ...patch } : tbl)) })

  return (
    <Flex vertical gap={16}>
      <Card
        title={
          <Flex gap={8} align="center" wrap>
            <Link to="/templates">
              <Button icon={<ArrowLeftOutlined />}>{t('模板')}</Button>
            </Link>
            <Typography.Text strong>{isNew ? t('新建模板') : template.title}</Typography.Text>
            {isNew ? <Tag color="orange">{t('未保存')}</Tag> : <Tag>{t('第 {{version}} 版', { version: template.version })}</Tag>}
            {template.archived && <Tag>{t('已归档')}</Tag>}
            <LabelTags
              template={{ ...template, domains: draft.domains, countries: draft.countries, tags: draft.tags }}
            />
            {template.kind !== 'mine' && <Tag color="blue">{t('只读（{{kind}}）', { kind: template.kind === 'starter' ? t('起步模板') : t('内置模板') })}</Tag>}
          </Flex>
        }
        extra={
          !readOnly && (
            isNew ? (
              <Space>
                <Link to="/templates">
                  <Button>{t('取消')}</Button>
                </Link>
                <Button
                  type="primary"
                  icon={<SaveOutlined />}
                  disabled={problem !== null}
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  {t('保存')}
                </Button>
              </Space>
            ) : (
              <Space.Compact>
                <Input
                  placeholder={t('本版本修改说明（选填）')}
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
                  {t('保存为第 {{version}} 版', { version: template.version + 1 })}
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
            title={t('填写字段后点击右上角“保存”，模板才会添加到我的模板')}
            description={t('未保存就离开此页面，内容将不会保留。')}
          />
        )}
        {template.used > 0 && !readOnly && (
          <Tip
            id="editor-template-used"
            style={{ marginBottom: 12 }}
            title={t('已有 {{used}} 份文档使用此模板', { used: template.used })}
            description={t('保存新版本不会改变这些文档已有的结果；如需按新版本重新抽取，请在审核页点击“重新处理”。')}
          />
        )}
        {!readOnly && <StarterFormulas template={template} draft={draft} set={set} />}
        <Descriptions column={{ xs: 1, md: 3 }} size="small">
          <Descriptions.Item label={language === primary ? t('模板名称') : t('模板名（{{languageName}}）', { languageName: languageName(language) })}>
            {language === primary ? (
              <Input value={draft.title} disabled={readOnly} onChange={(e) => set({ title: e.target.value })} />
            ) : (
              <Input
                value={draft.titles?.[language] ?? ''}
                placeholder={draft.title}
                disabled={readOnly}
                onChange={(e) => set({ titles: { ...draft.titles, [language]: e.target.value } })}
              />
            )}
          </Descriptions.Item>
          <Descriptions.Item
            label={language === primary ? t('说明') : t('说明（{{languageName}}）', { languageName: languageName(language) })}
            span={2}
          >
            {language === primary ? (
              <Input
                value={draft.description}
                disabled={readOnly}
                onChange={(e) => set({ description: e.target.value })}
              />
            ) : (
              <Input
                value={draft.descriptions?.[language] ?? ''}
                placeholder={draft.description}
                disabled={readOnly}
                onChange={(e) => set({ descriptions: { ...draft.descriptions, [language]: e.target.value } })}
              />
            )}
          </Descriptions.Item>
          <Descriptions.Item
            label={
              <Tooltip title={t('名称和说明均以此语言编写；审核页和导出默认显示此语言的名称，提供给模型的说明也使用此语言')}>
                {t('主语言')}
              </Tooltip>
            }
          >
            <Select
              size="small"
              aria-label={t('主语言')}
              value={primary}
              disabled={readOnly}
              options={languageOptions(editorLanguages)}
              onChange={changePrimary}
              style={{ minWidth: 150 }}
              showSearch
              optionFilterProp="label"
            />
          </Descriptions.Item>
        </Descriptions>
        <div style={{ marginTop: 8, maxWidth: 900 }}>
          <LabelFields
            domains={draft.domains}
            countries={draft.countries}
            tags={draft.tags}
            disabled={readOnly}
            onChange={(patch) =>
              set({ ...patch, ...(patch.domains ? { category: patch.domains[0] ?? '' } : {}) })
            }
          />
        </div>
      </Card>

      <Card
        title={t('字段（{{length}}）', { length: draft.fields.length })}
        size="small"
        extra={
          <Flex align="center" gap={8}>
            <Tooltip title={t('名称和说明可填写多种语言，在审核页、导出和预览中切换；未填写的语言显示主语言内容。各语言名称也会提供给模型，作为单据上可能出现的写法')}>
              <Typography.Text type="secondary">{t('编辑语言')}</Typography.Text>
            </Tooltip>
            <Select
              size="small"
              aria-label={t('编辑语言')}
              value={language}
              options={languageOptions(editorLanguages)}
              onChange={setLanguage}
              style={{ minWidth: 150 }}
              showSearch
              optionFilterProp="label"
            />
          </Flex>
        }
      >
        <FieldsEditor
          language={language}
          primary={primary}
          fields={draft.fields}
          onChange={(fields) => set({ fields })}
          locked={locked}
          readOnly={readOnly}
          scope={{ fields: draft.fields, tables: draft.tables, row: null }}
        />
      </Card>

      <Card
        title={t('表格（{{length}}）', { length: draft.tables.length })}
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
                      name: draft.tables.some((tbl) => tbl.name === 'items') ? `table_${draft.tables.length + 1}` : 'items',
                      label: t('明细'),
                      description: '',
                      required: false,
                      export: true,
                      columns: [{ ...blankField([]), name: 'amount', label: t('金额'), type: 'number' }],
                    },
                  ],
                })
              }
            >
              {t('添加表格')}
            </Button>
          )
        }
      >
        {draft.tables.length === 0 ? (
          <Empty description={t('暂无表格。行数不固定的内容（如商品明细、航段）请使用表格')} />
        ) : (
          <Flex vertical gap={16}>
            {draft.tables.map((tbl, i) => (
              <Card
                key={i}
                type="inner"
                size="small"
                title={
                  <Flex gap={8} align="center" wrap>
                    {language === primary ? (
                      <Input
                        value={tbl.label}
                        disabled={readOnly}
                        onChange={(e) => setTable(i, { label: e.target.value })}
                        style={{ width: 200 }}
                        addonBefore={languageName(primary)}
                      />
                    ) : (
                      <Input
                        value={tbl.names?.[language] ?? ''}
                        placeholder={tbl.label}
                        disabled={readOnly}
                        onChange={(e) => setTable(i, { names: { ...tbl.names, [language]: e.target.value } })}
                        style={{ width: 200 }}
                        addonBefore={languageName(language)}
                      />
                    )}
                    <Input
                      value={tbl.name}
                      disabled={readOnly || lockedTables.has(tbl.name)}
                      status={FIELD_ID.test(tbl.name) ? undefined : 'error'}
                      onChange={(e) => setTable(i, { name: e.target.value.trim() })}
                      style={{ width: 160 }}
                      addonBefore="ID"
                    />
                    <Checkbox checked={tbl.required} disabled={readOnly} onChange={(e) => setTable(i, { required: e.target.checked })}>
                      {t('至少一行')}
                    </Checkbox>
                    <Checkbox checked={tbl.export} disabled={readOnly} onChange={(e) => setTable(i, { export: e.target.checked })}>
                      {t('导出')}
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
                      {t('删除此表格')}
                    </Button>
                  )
                }
              >
                {language === primary ? (
                  <Input.TextArea
                    value={tbl.description}
                    disabled={readOnly}
                    autoSize={{ minRows: 1, maxRows: 3 }}
                    placeholder={t('给模型的说明：表格在单据中的位置、哪些行不计入（小计、合计等）')}
                    onChange={(e) => setTable(i, { description: e.target.value })}
                    style={{ marginBottom: 8 }}
                  />
                ) : (
                  <Input.TextArea
                    value={tbl.descriptions?.[language] ?? ''}
                    disabled={readOnly}
                    autoSize={{ minRows: 1, maxRows: 3 }}
                    placeholder={tbl.description}
                    aria-label={t('说明（{{languageName}}）', { languageName: languageName(language) })}
                    onChange={(e) => setTable(i, { descriptions: { ...tbl.descriptions, [language]: e.target.value } })}
                    style={{ marginBottom: 8 }}
                  />
                )}
                <FieldsEditor
                  language={language}
                  primary={primary}
                  fields={tbl.columns}
                  onChange={(columns) => setTable(i, { columns })}
                  locked={
                    new Set(
                      saved
                        ? (template.tables.find((x) => x.name === tbl.name)?.columns.map((c) => c.name) ?? [])
                        : [],
                    )
                  }
                  readOnly={readOnly}
                  scope={{ fields: draft.fields, tables: draft.tables, row: tbl.name }}
                />
              </Card>
            ))}
          </Flex>
        )}
      </Card>

      <RulesCard
        template={template}
        draft={draft}
        set={set}
        rules={rules.data ?? []}
        loading={rules.isPending}
        readOnly={readOnly}
      />

      {(template.workbook || (template.entities ?? []).length > 0 || (template.references ?? []).length > 0) && (
        <StructureCard
          template={template}
          fields={draft.fields}
          tables={draft.tables}
          columnRefs={draft.column_refs ?? {}}
          readOnly={readOnly}
          onChangeRef={(key, ref) => set({ column_refs: { ...(draft.column_refs ?? {}), [key]: ref } })}
        />
      )}

      {!readOnly && !isNew && (
        <Card
          size="small"
          title={
            <Space>
              <ExperimentOutlined />
              {t('试跑')}
            </Space>
          }
        >
          <Typography.Paragraph type="secondary">
            {t('使用')}<b>{t('已保存的最新版本')}</b>{t('（第 {{version}} 版）对一份已处理的文档执行一次抽取，以预览结果。结果不保存，也不修改文档。需等待模型运行完成，每页约十几秒。', { version: template.version })}
          </Typography.Paragraph>
          <Flex gap={8} wrap style={{ marginBottom: 12 }}>
            <Select
              style={{ minWidth: 320 }}
              placeholder={t('选择文档')}
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
              {t('试跑')}
            </Button>
            {dirty && <Typography.Text type="warning">{t('存在未保存的修改：试跑使用已保存的版本')}</Typography.Text>}
          </Flex>
          {trial.data && <TrialView result={trial.data} />}
        </Card>
      )}

      {template.kind === 'mine' && !isNew && (
        <Card size="small" title={t('历史版本')}>
          <List
            size="small"
            dataSource={template.versions}
            renderItem={(v) => (
              <List.Item
                actions={[
                  <Button key="view" type="link" onClick={() => setViewing(v.version)}>
                    {t('查看')}
                  </Button>,
                ]}
              >
                <Space>
                  <Tag color={v.version === template.version ? 'purple' : undefined}>{t('第 {{version}} 版', { version: v.version })}</Tag>
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
        title={t('第 {{value}} 版（只读）', { value: viewing ?? '' })}
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
              ...oldVersion.data.tables.flatMap((tbl) =>
                tbl.columns.map((c) => ({ ...c, name: `${tbl.name}.${c.name}`, label: `${tbl.label} · ${c.label}` })),
              ),
            ]}
            columns={[
              { title: t('名称'), dataIndex: 'label' },
              { title: t('字段 ID'), dataIndex: 'name' },
              { title: t('类型'), dataIndex: 'type' },
              { title: t('必填'), dataIndex: 'required', render: (r: boolean) => (r ? t('是') : '') },
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
  const { t } = useTranslation()
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        {t('第 {{version}} 版，耗时 {{toFixed}} 秒', { version: result.version, toFixed: (result.duration_ms / 1000).toFixed(1) })}
      </Typography.Text>
      {result.pages.map((page) => (
        <Flex key={page.page} vertical gap={8}>
          {result.pages.length > 1 && <Typography.Text strong>{t('第 {{page}} 页', { page: page.page })}</Typography.Text>}
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
              { title: t('字段'), dataIndex: 'label', width: 200 },
              { title: t('抽出的值'), dataIndex: 'value', render: (v: string | null) => v ?? <Typography.Text type="secondary">{t('（空）')}</Typography.Text> },
              {
                title: t('出处'),
                dataIndex: 'status',
                width: 120,
                render: (s: string) => <Tag color={STATUS_TEXT[s]?.[1]}>{STATUS_TEXT[s] ? t(STATUS_TEXT[s][0]) : s}</Tag>,
              },
            ]}
          />
        </Flex>
      ))}
    </Flex>
  )
}

/**
 * Copies of a starter template: the starter template has since added formulas to some fields (for example
 * nights = check-out − check-in on hotel bills) that this copy lacks. Offer "Apply" to fill them into the
 * editor, then save a new version.
 */
function StarterFormulas({
  template,
  draft,
  set,
}: {
  template: Template
  draft: TemplateIn
  set: (patch: Partial<TemplateIn>) => void
}) {
  const { t } = useTranslation()
  const labels = new Map<string, string>()
  for (const f of draft.fields) labels.set(f.name, f.label)
  for (const tbl of draft.tables) for (const c of tbl.columns) labels.set(`${tbl.name}.${c.name}`, t('{{label}}的{{label2}}', { label: tbl.label, label2: c.label }))
  const current = (key: string) => {
    const [table, column] = key.split('.')
    if (column === undefined) return draft.fields.find((f) => f.name === key)?.compute
    return draft.tables.find((tbl) => tbl.name === table)?.columns.find((c) => c.name === column)?.compute
  }
  const pending = Object.entries(template.starter_formulas ?? {}).filter(
    ([key]) => labels.has(key) && !current(key)?.trim(),
  )
  if (pending.length === 0) return null
  const apply = () => {
    const formulas = new Map(pending)
    set({
      fields: draft.fields.map((f) => (formulas.has(f.name) ? { ...f, compute: formulas.get(f.name) } : f)),
      tables: draft.tables.map((tbl) => ({
        ...tbl,
        columns: tbl.columns.map((c) => {
          const formula = formulas.get(`${tbl.name}.${c.name}`)
          return formula ? { ...c, compute: formula } : c
        }),
      })),
    })
  }
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      title={t('起步模板已新增公式，此模板尚未同步')}
      description={
        <>
          {pending.map(([key, formula]) => (
            <div key={key}>
              {labels.get(key)} = <Typography.Text code>{formula}</Typography.Text>
            </div>
          ))}
          <div>{t('应用后，这些字段将按公式计算，不再由模型从单据中抽取。应用后请点击右上角保存。')}</div>
        </>
      }
      action={
        <Button size="small" type="primary" onClick={apply}>
          {t('应用')}
        </Button>
      }
    />
  )
}
