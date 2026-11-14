import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  InboxOutlined,
  MoreOutlined,
  PlusOutlined,
  TagsOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Dropdown,
  Empty,
  Flex,
  Modal,
  Row,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { ApiError } from '../../api/client'
import {
  analyzeExcel,
  archiveTemplate,
  createTemplate,
  deleteTemplate,
  exportTemplate,
  importTemplate,
  listStarters,
  listTemplates,
  setTemplateLabels,
} from '../../api/templates'
import type { Template } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'
import { languageName } from '../../utils/languages'
import { countryName } from './labels'
import { applyFilters, type Filters, NO_FILTERS, SOURCE_TEXT, sourceKind } from './filtering'
import { LabelFields, LabelTags } from './LabelWidgets'
import { TemplateFilters } from './TemplateFilters'
import { TemplatePreview } from './TemplatePreview'
import { Tip } from '../../components/Tip'
import { useTranslation } from 'react-i18next'
import { t, uiLanguage } from '../../i18n'

/** Counts of fields, tables and rules (a small line on each card). */
/**
 * Starter templates grouped by region: generic (no country) first, then China and Australia, then the rest by
 * country name.
 */
function regionGroups(templates: Template[]): [string, Template[]][] {
  const groups = new Map<string, Template[]>()
  for (const tpl of templates) {
    const region = tpl.countries?.[0] ?? ''
    groups.set(region, [...(groups.get(region) ?? []), tpl])
  }
  const first = ['', 'CN', 'AU']
  const rank = (r: string) => (first.includes(r) ? first.indexOf(r) : first.length)
  return [...groups].sort(
    ([a], [b]) => rank(a) - rank(b) || countryName(a).localeCompare(countryName(b), uiLanguage()),
  )
}

function counts(template: Template): string {
  const columns = template.tables.reduce((n, table) => n + table.columns.length, 0)
  const tables = template.tables.length > 0 ? t(' · {{length}} 张表（{{columns}} 列）', { length: template.tables.length, columns }) : ''
  return t('{{length}} 个字段{{tables}} · {{length2}} 条规则', { length: template.fields.length, tables, length2: template.rules.length })
}

/**
 * Templates page: my templates + the starter library.
 * Uploads can only use my templates; starter templates must first be added to my templates (a copy that can
 * then be changed freely).
 */
export function TemplatesPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'starters' ? 'starters' : 'mine'
  const [showArchived, setShowArchived] = useState(false)
  const [mineFilters, setMineFilters] = useState<Filters>(NO_FILTERS)
  const [starterFilters, setStarterFilters] = useState<Filters>(NO_FILTERS)
  const [labelling, setLabelling] = useState<Template | null>(null)
  const [previewing, setPreviewing] = useState<Template | null>(null)
  // Selection: my templates by id, starter templates by name.
  const [pickedMine, setPickedMine] = useState<Set<string>>(new Set())
  const [pickedStarters, setPickedStarters] = useState<Set<string>>(new Set())
  const toggle = (set: Set<string>, key: string, on: boolean) => {
    const next = new Set(set)
    if (on) next.add(key)
    else next.delete(key)
    return next
  }
  const canManage = can('template:manage')

  const mine = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const starters = useQuery({
    queryKey: ['templates', 'starters'],
    queryFn: listStarters,
    staleTime: Infinity,
  })
  const onError = (err: unknown) =>
    void message.error(err instanceof ApiError ? err.message : String(err))

  const create = useMutation({
    mutationFn: createTemplate,
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t('已添加「{{title}}」到我的模板', { title: tpl.title }))
      void navigate(`/templates/${tpl.id}`)
    },
    onError,
  })
  const archive = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      archiveTemplate(id, archived),
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(tpl.archived ? t('已归档「{{title}}」', { title: tpl.title }) : t('已恢复「{{title}}」', { title: tpl.title }))
    },
    onError,
  })

  const exportOne = useMutation({
    mutationFn: ({ t, format }: { t: Template; format: 'xlsx' | 'json' }) =>
      exportTemplate(t.id ?? t.name, format),
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
    onError,
  })
  const importJson = useMutation({
    mutationFn: importTemplate,
    onSuccess: (tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t('已导入「{{title}}」', { title: tpl.title }))
    },
    onError,
  })
  /**
   * Import: .json is imported directly; .xlsx is inspected first: a standard template workbook is imported
   * after confirmation, while a business report (the workbook a company has to submit) opens the preview to
   * confirm how each sheet is used before creating the template.
   */
  const importOne = useMutation({
    mutationFn: async (file: File) => {
      if (file.name.toLowerCase().endsWith('.json')) {
        let data: unknown
        try {
          data = JSON.parse(await file.text())
        } catch {
          throw new Error(t('{{name}} 不是有效的模板导出文件（JSON 格式错误）', { name: file.name }))
        }
        await importJson.mutateAsync(data)
        return
      }
      const analysis = await analyzeExcel(file)
      if (analysis.kind === 'report' && analysis.upload_id) {
        void navigate(`/templates/excel/${analysis.upload_id}`)
        return
      }
      const template = analysis.template
      if (!template) return
      modal.confirm({
        title: t('导入模板「{{title}}」？', { title: template.title }),
        width: 560,
        content: (
          <Flex vertical gap={8}>
            <span>
              {t('此文件为标准模板 Excel：{{length}} 个字段、{{length2}} 张表格、{{length3}} 条规则。导入后将创建一个新模板（第 1 版）。', { length: template.fields.length, length2: template.tables.length, length3: template.rules.length + (template.custom_rules?.length ?? 0) })}
            </span>
            {(analysis.ignored ?? []).length > 0 && (
              <Alert
                type="warning"
                showIcon
                title={t('以下内容无法识别，不会导入；如为拼写错误，请修改表头后重新上传')}
                description={
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {(analysis.ignored ?? []).map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                }
              />
            )}
          </Flex>
        ),
        okText: t('导入'),
        cancelText: t('取消'),
        onOk: () => importJson.mutateAsync(template),
      })
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (tpl: Template) => deleteTemplate(tpl.id ?? ''),
    onSuccess: (_, tpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t('已删除「{{title}}」', { title: tpl.title }))
    },
    onError,
  })

  /** Bulk delete my templates one by one, then report how many were deleted and which failed. */
  const removeMany = useMutation({
    mutationFn: async (list: Template[]) => {
      const failed: string[] = []
      for (const tpl of list) {
        try {
          await deleteTemplate(tpl.id ?? '')
        } catch (err) {
          failed.push(t('{{title}}（{{value}}）', { title: tpl.title, value: err instanceof ApiError ? err.message : String(err) }))
        }
      }
      return { done: list.length - failed.length, failed }
    },
    onSuccess: ({ done, failed }) => {
      setPickedMine(new Set())
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      if (failed.length) void message.warning(t('已删除 {{done}} 个；未能删除：{{failed}}', { done, failed: failed.join(t('、')) }))
      else void message.success(t('已删除 {{done}} 个模板', { done }))
    },
    onError,
  })
  /** Bulk add starter templates to my templates; already added ones are skipped. */
  const addMany = useMutation({
    mutationFn: async (list: Template[]) => {
      const failed: string[] = []
      for (const tpl of list) {
        try {
          await createTemplate({ source: 'starter', starter: tpl.name })
        } catch (err) {
          failed.push(t('{{title}}（{{value}}）', { title: tpl.title, value: err instanceof ApiError ? err.message : String(err) }))
        }
      }
      return { done: list.length - failed.length, failed }
    },
    onSuccess: ({ done, failed }) => {
      setPickedStarters(new Set())
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      if (failed.length) void message.warning(t('已添加 {{done}} 个；未能添加：{{failed}}', { done, failed: failed.join(t('、')) }))
      else void message.success(t('已将 {{done}} 个模板添加到我的模板', { done }))
    },
    onError,
  })

  function askDeleteMany(list: Template[]) {
    const used = list.filter((tpl) => tpl.used > 0).length
    modal.confirm({
      title: t('删除选中的 {{length}} 个模板？', { length: list.length }),
      width: 520,
      content: (
        <Flex vertical gap={8}>
          <Typography.Text>
            {list.slice(0, 10).map((tpl) => t('「{{title}}」', { title: tpl.title })).join(t('、'))}
            {list.length > 10 ? t(' 等 {{length}} 个', { length: list.length }) : ''}
          </Typography.Text>
          <Typography.Text type="secondary">
            {t('删除后将不再出现在模板列表和上传选项中，且无法恢复。')}
            {used > 0 ? t('其中 {{used}} 个已被文档使用，这些文档仍可正常查看和导出。', { used }) : ''}
            {t('删除前可先导出备份。')}
          </Typography.Text>
        </Flex>
      ),
      okText: t('删除 {{length}} 个', { length: list.length }),
      okButtonProps: { danger: true },
      cancelText: t('取消'),
      onOk: () => removeMany.mutateAsync(list),
    })
  }

  function askAddMany(list: Template[]) {
    const fresh = list.filter((tpl) => !added.has(`starter:${tpl.name}`))
    const skipped = list.length - fresh.length
    if (fresh.length === 0) {
      void message.info(t('所选模板均已添加'))
      return
    }
    modal.confirm({
      title: t('将 {{length}} 个起步模板添加到我的模板？', { length: fresh.length }),
      width: 520,
      content: (
        <Flex vertical gap={8}>
          <Typography.Text>
            {fresh.slice(0, 10).map((tpl) => t('「{{title}}」', { title: tpl.title })).join(t('、'))}
            {fresh.length > 10 ? t(' 等 {{length}} 个', { length: fresh.length }) : ''}
          </Typography.Text>
          {skipped > 0 && (
            <Typography.Text type="secondary">{t('另有 {{skipped}} 个已添加，将跳过（如需再添加一份，请点击卡片上的“添加到我的模板”）。', { skipped })}</Typography.Text>
          )}
          <Typography.Text type="secondary">{t('每个模板将复制为你的模板（第 1 版），之后的修改不影响原版。')}</Typography.Text>
        </Flex>
      ),
      okText: t('添加 {{length}} 个', { length: fresh.length }),
      cancelText: t('取消'),
      onOk: () => addMany.mutateAsync(fresh),
    })
  }

  function askDelete(template: Template) {
    modal.confirm({
      title: t('删除「{{title}}」？', { title: template.title }),
      content:
        t('删除后，此模板将不再出现在模板列表和上传选项中，且无法恢复。已使用此模板的文档不受影响，仍可正常查看和导出。删除前可先导出备份。'),
      okText: t('删除'),
      okButtonProps: { danger: true },
      cancelText: t('取消'),
      onOk: () => remove.mutateAsync(template),
    })
  }

  const own = (mine.data ?? []).filter((tpl) => tpl.kind === 'mine')
  const visible = own.filter((tpl) => showArchived || !tpl.archived)
  const shown = applyFilters(visible, mineFilters)
  const startersShown = applyFilters(starters.data ?? [], starterFilters)
  const archivedCount = own.filter((tpl) => tpl.archived).length
  const added = new Set(own.map((tpl) => tpl.source))
  const pickedMineShown = shown.filter((tpl) => pickedMine.has(tpl.id ?? ''))
  const pickedStartersShown = startersShown.filter((tpl) => pickedStarters.has(tpl.name))
  /** My templates copied from a starter template show the starter template's basis in the preview. */
  const basisOf = (tpl: Template) =>
    tpl.kind === 'mine' && tpl.source.startsWith('starter:')
      ? starters.data?.find((s) => `starter:${s.name}` === tpl.source)?.basis
      : tpl.basis

  const mineList = (
    <Flex vertical gap={16}>
      <Flex gap={8} wrap justify="space-between">
        <Flex gap={8} wrap>
          {canManage && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => void navigate('/templates/new')}
            >
              {t('新建空白模板')}
            </Button>
          )}
          <Button onClick={() => setParams({ tab: 'starters' })}>{t('从起步模板库添加')}</Button>
          {canManage && (
            <Upload
              accept=".xlsx,.json"
              showUploadList={false}
              beforeUpload={(file) => {
                importOne.mutate(file)
                return false
              }}
            >
              <Tooltip title={t('选择需要提交的 Excel 表格，按其版式创建模板（导出时回填至该表格）；也可选择导出的模板文件（标准 Excel 或 JSON）')}>
                <Button icon={<UploadOutlined />} loading={importOne.isPending}>
                  {t('从 Excel 创建 / 导入模板')}
                </Button>
              </Tooltip>
            </Upload>
          )}
        </Flex>
        {archivedCount > 0 && (
          <Button type="link" onClick={() => setShowArchived((on) => !on)}>
            {showArchived ? t('隐藏已归档') : t('显示已归档（{{archivedCount}}）', { archivedCount })}
          </Button>
        )}
      </Flex>
      {visible.length > 0 && (
        <TemplateFilters templates={visible} value={mineFilters} onChange={setMineFilters} mine />
      )}
      {canManage && shown.length > 0 && (
        <Flex gap={8} align="center" wrap style={{ minHeight: 32 }}>
          <Checkbox
            checked={pickedMineShown.length === shown.length}
            indeterminate={pickedMineShown.length > 0 && pickedMineShown.length < shown.length}
            onChange={(e) => setPickedMine(new Set(e.target.checked ? shown.map((tpl) => tpl.id ?? '') : []))}
          >
            {t('全选（{{length}}）', { length: shown.length })}
          </Checkbox>
          {pickedMineShown.length > 0 && (
            <>
              <Typography.Text type="secondary">{t('已选 {{length}} 个', { length: pickedMineShown.length })}</Typography.Text>
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={removeMany.isPending}
                onClick={() => askDeleteMany(pickedMineShown)}
              >
                {t('删除选中的 {{length}} 个', { length: pickedMineShown.length })}
              </Button>
              <Button type="link" onClick={() => setPickedMine(new Set())}>
                {t('取消选择')}
              </Button>
            </>
          )}
        </Flex>
      )}
      <Tip
        id="templates-from-sample"
        title={t('也可以通过样例创建模板')}
        description={t('如需按固定格式提交 Excel（如报销表、登记表），可点击“从 Excel 创建”并选择该表格，按其版式创建模板，导出时直接回填。也可在上传时选择“抽取所有键值对”，处理完成后在审核页将所需字段保存为模板。')}
      />
      {visible.length === 0 ? (
        <Empty description={t('暂无模板。可从起步模板库添加，或新建空白模板')} />
      ) : shown.length === 0 ? (
        <Empty description={t('没有符合筛选条件的模板')}>
          <Button onClick={() => setMineFilters(NO_FILTERS)}>{t('清空筛选')}</Button>
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {shown.map((tpl) => (
            <Col key={tpl.id} xs={24} md={12} xl={8}>
              <Card
                size="small"
                title={
                  <Flex gap={8} align="center">
                    {canManage && (
                      <Checkbox
                        aria-label={t('选择「{{title}}」', { title: tpl.title })}
                        checked={pickedMine.has(tpl.id ?? '')}
                        onChange={(e) => setPickedMine((s) => toggle(s, tpl.id ?? '', e.target.checked))}
                      />
                    )}
                    <Typography.Text strong ellipsis>
                      {tpl.title}
                    </Typography.Text>
                  </Flex>
                }
                extra={tpl.archived ? <Tag>{t('已归档')}</Tag> : <Tag>{t('第 {{version}} 版', { version: tpl.version })}</Tag>}
                actions={[
                  <Button key="preview" type="link" icon={<EyeOutlined />} onClick={() => setPreviewing(tpl)}>
                    {t('预览')}
                  </Button>,
                  <Button
                    key="edit"
                    type="link"
                    icon={<EditOutlined />}
                    onClick={() => void navigate(`/templates/${tpl.id}`)}
                  >
                    {canManage ? t('编辑') : t('查看')}
                  </Button>,
                  ...(canManage
                    ? [
                        <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t: tpl, format })} />,
                        <Dropdown
                          key="more"
                          menu={{
                            items: [
                              { key: 'copy', icon: <CopyOutlined />, label: t('创建副本') },
                              { key: 'labels', icon: <TagsOutlined />, label: t('编辑标签') },
                              {
                                key: 'archive',
                                icon: <InboxOutlined />,
                                label: tpl.archived ? t('恢复') : t('归档（不再出现在上传选项中）'),
                              },
                              { key: 'delete', icon: <DeleteOutlined />, label: t('删除'), danger: true },
                            ],
                            onClick: ({ key }) =>
                              key === 'delete'
                                ? askDelete(tpl)
                                : key === 'copy'
                                  ? create.mutate({ source: 'copy', copy_of: tpl.id ?? '' })
                                  : key === 'labels'
                                    ? setLabelling(tpl)
                                    : archive.mutate({ id: tpl.id ?? '', archived: !tpl.archived }),
                          }}
                        >
                          <Button type="link" icon={<MoreOutlined />} aria-label={t('更多')}>
                            {t('更多')}
                          </Button>
                        </Dropdown>,
                      ]
                    : [
                        <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t: tpl, format })} />,
                      ]),
                ]}
              >
                <Flex wrap gap={4} style={{ marginBottom: 8 }}>
                  <LabelTags template={tpl} />
                </Flex>
                <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ minHeight: 44 }}>
                  {tpl.description || t('（无说明）')}
                </Typography.Paragraph>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {counts(tpl)}
                  {tpl.updated_at ? t(' · 修改于 {{formatTime}}', { formatTime: formatTime(tpl.updated_at) }) : ''}
                </Typography.Text>
                <br />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {SOURCE_TEXT[sourceKind(tpl.source)] ?? ''}
                  {tpl.used ? t(' · 已用于 {{used}} 份文档', { used: tpl.used }) : t(' · 尚未被文档使用')}
                  {tpl.last_used_at ? t('，最近 {{formatTime}}', { formatTime: formatTime(tpl.last_used_at) }) : ''}
                </Typography.Text>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </Flex>
  )

  const starterList = (
    <Flex vertical gap={16}>
      <Typography.Text type="secondary">
        {t('添加到我的模板后可自由修改，不影响原版。')}
      </Typography.Text>
      <TemplateFilters
        templates={starters.data ?? []}
        value={starterFilters}
        onChange={setStarterFilters}
        mine={false}
      />
      {canManage && startersShown.length > 0 && (
        <Flex gap={8} align="center" wrap style={{ minHeight: 32 }}>
          <Checkbox
            checked={pickedStartersShown.length === startersShown.length}
            indeterminate={pickedStartersShown.length > 0 && pickedStartersShown.length < startersShown.length}
            onChange={(e) => setPickedStarters(new Set(e.target.checked ? startersShown.map((tpl) => tpl.name) : []))}
          >
            {t('全选（{{length}}）', { length: startersShown.length })}
          </Checkbox>
          {pickedStartersShown.length > 0 && (
            <>
              <Typography.Text type="secondary">{t('已选 {{length}} 个', { length: pickedStartersShown.length })}</Typography.Text>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                loading={addMany.isPending}
                onClick={() => askAddMany(pickedStartersShown)}
              >
                {t('添加选中的 {{length}} 个到我的模板', { length: pickedStartersShown.length })}
              </Button>
              <Button type="link" onClick={() => setPickedStarters(new Set())}>
                {t('取消选择')}
              </Button>
            </>
          )}
        </Flex>
      )}
      {/* Grouped by region: generic first, then China and Australia, then by country name. */}
      {regionGroups(startersShown).map(([region, group]) => (
        <Flex key={region} vertical gap={8}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {region ? countryName(region) : t('通用（适用于所有国家）')}
            <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginInlineStart: 8 }}>
              {t('{{length}} 个', { length: group.length })}
            </Typography.Text>
          </Typography.Title>
          <Row gutter={[16, 16]}>
            {group.map((tpl) => (
              <Col key={tpl.name} xs={24} md={12} xl={8}>
                <Card
                  size="small"
                  title={
                    <Flex gap={8} align="center">
                      {canManage && (
                        <Checkbox
                          aria-label={t('选择「{{title}}」', { title: tpl.title })}
                          checked={pickedStarters.has(tpl.name)}
                          onChange={(e) => setPickedStarters((s) => toggle(s, tpl.name, e.target.checked))}
                        />
                      )}
                      <Typography.Text strong ellipsis>
                        {tpl.title}
                      </Typography.Text>
                    </Flex>
                  }
                  extra={added.has(`starter:${tpl.name}`) ? <Tag color="green">{t('已添加')}</Tag> : null}
                  actions={[
                    <Button key="preview" type="link" icon={<EyeOutlined />} onClick={() => setPreviewing(tpl)}>
                      {t('预览')}
                    </Button>,
                    <Tooltip key="add" title={canManage ? undefined : t('没有“管理模板”权限')}>
                      <Button
                        type="link"
                        icon={<PlusOutlined />}
                        disabled={!canManage}
                        loading={create.isPending && create.variables?.starter === tpl.name}
                        onClick={() => create.mutate({ source: 'starter', starter: tpl.name })}
                      >
                        {t('添加')}
                      </Button>
                    </Tooltip>,
                    <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t: tpl, format })} />,
                  ]}
                >
                  <Flex wrap gap={4} style={{ marginBottom: 8 }}>
                    <LabelTags template={tpl} custom={false} />
                  </Flex>
                  <Typography.Paragraph type="secondary" ellipsis={{ rows: 3 }} style={{ minHeight: 66 }}>
                    {tpl.description}
                  </Typography.Paragraph>
                  {(tpl.languages?.length ?? 0) > 1 && (
                    <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
                      {t('名称语言：')}
                      {(tpl.languages?.length ?? 0) > 4
                        ? t('{{join}} 等 {{length}} 种', { join: (tpl.languages ?? []).slice(0, 2).map(languageName).join(' · '), length: tpl.languages?.length })
                        : (tpl.languages ?? []).map(languageName).join(' · ')}
                    </Typography.Paragraph>
                  )}
                  <Typography.Paragraph style={{ fontSize: 12, marginBottom: 4 }} ellipsis={{ rows: 2 }}>
                    {tpl.fields.map((f) => f.label).join(t('、'))}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {counts(tpl)}
                  </Typography.Text>
                </Card>
              </Col>
            ))}
          </Row>
        </Flex>
      ))}
    </Flex>
  )

  return (
    <Card
      title={t('模板')}
      extra={
        <Segmented
          value={tab}
          onChange={(value) => setParams(value === 'starters' ? { tab: 'starters' } : {})}
          options={[
            { label: t('我的模板（{{length}}）', { length: own.filter((tpl) => !tpl.archived).length }), value: 'mine' },
            { label: t('起步模板库'), value: 'starters' },
          ]}
        />
      }
    >
      {mine.isPending || starters.isPending ? (
        <Flex justify="center" style={{ padding: 48 }}>
          <Spin />
        </Flex>
      ) : tab === 'mine' ? (
        mineList
      ) : (
        starterList
      )}
      {labelling && (
        <LabelsDialog
          template={labelling}
          onClose={() => setLabelling(null)}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: ['templates'] })}
        />
      )}
      <TemplatePreview
        key={previewing ? (previewing.id ?? previewing.name) : 'none'}
        template={previewing}
        basis={previewing ? basisOf(previewing) : undefined}
        onClose={() => setPreviewing(null)}
        actions={
          previewing && (
            <Flex gap={8} justify="flex-end" wrap>
              <ExportMenu onExport={(format) => exportOne.mutate({ t: previewing, format })} />
              <Button onClick={() => setPreviewing(null)}>{t('关闭')}</Button>
              {previewing.kind === 'mine' ? (
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => void navigate(`/templates/${previewing.id}`)}
                >
                  {canManage ? t('编辑') : t('查看详情')}
                </Button>
              ) : (
                <Tooltip title={canManage ? undefined : t('没有“管理模板”权限')}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    disabled={!canManage}
                    loading={create.isPending}
                    onClick={() => create.mutate({ source: 'starter', starter: previewing.name })}
                  >
                    {added.has(`starter:${previewing.name}`) ? t('再添加一份到我的模板') : t('添加到我的模板')}
                  </Button>
                </Tooltip>
              )}
            </Flex>
          )
        }
      />
    </Card>
  )
}

/** Change only tags (domains, countries and regions, custom tags); no new version. */
function LabelsDialog({
  template,
  onClose,
  onSaved,
}: {
  template: Template
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [labels, setLabels] = useState({
    domains: template.domains ?? (template.category ? [template.category] : []),
    countries: template.countries ?? [],
    tags: template.tags ?? [],
  })
  const save = useMutation({
    mutationFn: () => setTemplateLabels(template.id ?? '', labels),
    onSuccess: () => {
      void message.success(t('已更新「{{title}}」的标签', { title: template.title }))
      onSaved()
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  return (
    <Modal
      open
      title={t('「{{title}}」的标签', { title: template.title })}
      okText={t('保存')}
      cancelText={t('取消')}
      confirmLoading={save.isPending}
      onOk={() => save.mutate()}
      onCancel={onClose}
      width={640}
    >
      <Typography.Paragraph type="secondary">
        {t('标签仅用于分类和筛选；修改标签不会生成新版本，也不影响已使用该模板的文档。')}
      </Typography.Paragraph>
      <LabelFields {...labels} onChange={(patch) => setLabels((l) => ({ ...l, ...patch }))} />
    </Modal>
  )
}

/**
 * Export a template: standard Excel (readable and editable, for re-import) or JSON (complete content,
 * including the original workbook layout).
 */
function ExportMenu({ onExport }: { onExport: (format: 'xlsx' | 'json') => void }) {
  const { t } = useTranslation()
  return (
    <Dropdown
      menu={{
        items: [
          { key: 'xlsx', label: t('Excel（可查看和编辑，修改后可再次导入）') },
          { key: 'json', label: t('JSON（完整内容，包括按原表导出的版式）') },
        ],
        onClick: ({ key }) => onExport(key as 'xlsx' | 'json'),
      }}
    >
      <Button type="link" icon={<DownloadOutlined />}>
        {t('导出')}
      </Button>
    </Dropdown>
  )
}
