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

/** 字段、表格、规则各有几个（卡片上的一行小字）。 */
/** 起步模板按地区分组：通用（没写国家）在前，然后中国、澳洲，其余按国家的中文名排。 */
function regionGroups(templates: Template[]): [string, Template[]][] {
  const groups = new Map<string, Template[]>()
  for (const t of templates) {
    const region = t.countries?.[0] ?? ''
    groups.set(region, [...(groups.get(region) ?? []), t])
  }
  const first = ['', 'CN', 'AU']
  const rank = (r: string) => (first.includes(r) ? first.indexOf(r) : first.length)
  return [...groups].sort(
    ([a], [b]) => rank(a) - rank(b) || countryName(a).localeCompare(countryName(b), 'zh-CN'),
  )
}

function counts(t: Template): string {
  const columns = t.tables.reduce((n, table) => n + table.columns.length, 0)
  const tables = t.tables.length > 0 ? ` · ${t.tables.length} 张表（${columns} 列）` : ''
  return `${t.fields.length} 个字段${tables} · ${t.rules.length} 条规则`
}

/**
 * 模板页（ADR 0030）：我的模板 + 起步模板库。
 * 上传时只能选"我的模板"；起步模板要先"添加到我的模板"（复制一份，之后随便改）。
 */
export function TemplatesPage() {
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
  // 勾选：我的模板按 id，起步模板按名字
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
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(`已添加「${t.title}」到我的模板`)
      void navigate(`/templates/${t.id}`)
    },
    onError,
  })
  const archive = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      archiveTemplate(id, archived),
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(t.archived ? `已归档「${t.title}」` : `已恢复「${t.title}」`)
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
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(`已导入「${t.title}」`)
    },
    onError,
  })
  /**
   * 导入：.json 直接导入；.xlsx 先看是什么——标准模板 Excel 确认后导入，
   * 业务报表（公司要交的那张表）打开预览页，确认每张 sheet 怎么用再建模板。
   */
  const importOne = useMutation({
    mutationFn: async (file: File) => {
      if (file.name.toLowerCase().endsWith('.json')) {
        let data: unknown
        try {
          data = JSON.parse(await file.text())
        } catch {
          throw new Error(`${file.name} 不是导出的模板文件（JSON 格式不对）`)
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
        title: `导入模板「${template.title}」？`,
        content: `这是标准模板 Excel：${template.fields.length} 个字段、${template.tables.length} 张表格、${template.rules.length} 条规则。导入后是一个新的我的模板（第 1 版）。`,
        okText: '导入',
        cancelText: '取消',
        onOk: () => importJson.mutateAsync(template),
      })
    },
    onError,
  })
  const remove = useMutation({
    mutationFn: (t: Template) => deleteTemplate(t.id ?? ''),
    onSuccess: (_, t) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void message.success(`已删除「${t.title}」`)
    },
    onError,
  })

  /** 批量删除我的模板：一个一个删，最后说删了几个、哪些没删成。 */
  const removeMany = useMutation({
    mutationFn: async (list: Template[]) => {
      const failed: string[] = []
      for (const t of list) {
        try {
          await deleteTemplate(t.id ?? '')
        } catch (err) {
          failed.push(`${t.title}（${err instanceof ApiError ? err.message : String(err)}）`)
        }
      }
      return { done: list.length - failed.length, failed }
    },
    onSuccess: ({ done, failed }) => {
      setPickedMine(new Set())
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      if (failed.length) void message.warning(`删除了 ${done} 个；没删成：${failed.join('、')}`)
      else void message.success(`删除了 ${done} 个模板`)
    },
    onError,
  })
  /** 批量把起步模板添加到我的模板：已经添加过的跳过。 */
  const addMany = useMutation({
    mutationFn: async (list: Template[]) => {
      const failed: string[] = []
      for (const t of list) {
        try {
          await createTemplate({ source: 'starter', starter: t.name })
        } catch (err) {
          failed.push(`${t.title}（${err instanceof ApiError ? err.message : String(err)}）`)
        }
      }
      return { done: list.length - failed.length, failed }
    },
    onSuccess: ({ done, failed }) => {
      setPickedStarters(new Set())
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      if (failed.length) void message.warning(`添加了 ${done} 个；没加成：${failed.join('、')}`)
      else void message.success(`添加了 ${done} 个模板到我的模板`)
    },
    onError,
  })

  function askDeleteMany(list: Template[]) {
    const used = list.filter((t) => t.used > 0).length
    modal.confirm({
      title: `删除选中的 ${list.length} 个模板？`,
      width: 520,
      content: (
        <Flex vertical gap={8}>
          <Typography.Text>
            {list.slice(0, 10).map((t) => `「${t.title}」`).join('、')}
            {list.length > 10 ? ` 等 ${list.length} 个` : ''}
          </Typography.Text>
          <Typography.Text type="secondary">
            删除后不再出现在模板列表和上传里，不能恢复。
            {used > 0 ? `其中 ${used} 个有文档用过，那些文档照常能看、能导出。` : ''}
            删除前可以先导出备份。
          </Typography.Text>
        </Flex>
      ),
      okText: `删除 ${list.length} 个`,
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => removeMany.mutateAsync(list),
    })
  }

  function askAddMany(list: Template[]) {
    const fresh = list.filter((t) => !added.has(`starter:${t.name}`))
    const skipped = list.length - fresh.length
    if (fresh.length === 0) {
      void message.info('选中的都已经添加过了')
      return
    }
    modal.confirm({
      title: `把 ${fresh.length} 个起步模板添加到我的模板？`,
      width: 520,
      content: (
        <Flex vertical gap={8}>
          <Typography.Text>
            {fresh.slice(0, 10).map((t) => `「${t.title}」`).join('、')}
            {fresh.length > 10 ? ` 等 ${fresh.length} 个` : ''}
          </Typography.Text>
          {skipped > 0 && (
            <Typography.Text type="secondary">另外 {skipped} 个已经添加过，跳过（要再加一份就点卡片上的"添加到我的模板"）。</Typography.Text>
          )}
          <Typography.Text type="secondary">每个都会复制一份成为你的模板（第 1 版），之后可以随便改，不影响原版。</Typography.Text>
        </Flex>
      ),
      okText: `添加 ${fresh.length} 个`,
      cancelText: '取消',
      onOk: () => addMany.mutateAsync(fresh),
    })
  }

  function askDelete(t: Template) {
    modal.confirm({
      title: `删除「${t.title}」？`,
      content:
        '删除后，这个模板不再出现在模板列表和上传里，不能恢复。用过它的文档不受影响，照常能看、能导出。删除前可以先导出备份。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => remove.mutateAsync(t),
    })
  }

  const own = (mine.data ?? []).filter((t) => t.kind === 'mine')
  const visible = own.filter((t) => showArchived || !t.archived)
  const shown = applyFilters(visible, mineFilters)
  const startersShown = applyFilters(starters.data ?? [], starterFilters)
  const archivedCount = own.filter((t) => t.archived).length
  const added = new Set(own.map((t) => t.source))
  const pickedMineShown = shown.filter((t) => pickedMine.has(t.id ?? ''))
  const pickedStartersShown = startersShown.filter((t) => pickedStarters.has(t.name))
  /** 我的模板从起步模板复制来的：预览时给它看起步模板的依据。 */
  const basisOf = (t: Template) =>
    t.kind === 'mine' && t.source.startsWith('starter:')
      ? starters.data?.find((s) => `starter:${s.name}` === t.source)?.basis
      : t.basis

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
              新建空白模板
            </Button>
          )}
          <Button onClick={() => setParams({ tab: 'starters' })}>从起步模板库添加</Button>
          {canManage && (
            <Upload
              accept=".xlsx,.json"
              showUploadList={false}
              beforeUpload={(file) => {
                importOne.mutate(file)
                return false
              }}
            >
              <Tooltip title="选公司要交的那张 Excel，按它的样子建模板（导出时填回它）；也可以选导出的模板文件（标准 Excel 或 JSON）">
                <Button icon={<UploadOutlined />} loading={importOne.isPending}>
                  从 Excel 建 / 导入模板
                </Button>
              </Tooltip>
            </Upload>
          )}
        </Flex>
        {archivedCount > 0 && (
          <Button type="link" onClick={() => setShowArchived((on) => !on)}>
            {showArchived ? '隐藏归档的' : `显示归档的（${archivedCount}）`}
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
            onChange={(e) => setPickedMine(new Set(e.target.checked ? shown.map((t) => t.id ?? '') : []))}
          >
            全选（{shown.length}）
          </Checkbox>
          {pickedMineShown.length > 0 && (
            <>
              <Typography.Text type="secondary">已选 {pickedMineShown.length} 个</Typography.Text>
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={removeMany.isPending}
                onClick={() => askDeleteMany(pickedMineShown)}
              >
                删除选中的 {pickedMineShown.length} 个
              </Button>
              <Button type="link" onClick={() => setPickedMine(new Set())}>
                取消选择
              </Button>
            </>
          )}
        </Flex>
      )}
      <Tip
        id="templates-from-sample"
        title="也可以用样例建模板"
        description='公司有固定要交的 Excel（报销表、登记表），点"从 Excel 建"选它，按它的样子建模板，导出时直接填回它。也可以上传时选"抽取所有键值对"，处理完在审核页面把需要的字段存成模板。'
      />
      {visible.length === 0 ? (
        <Empty description="还没有自己的模板。从起步模板库添加一个，或者新建空白模板" />
      ) : shown.length === 0 ? (
        <Empty description="没有符合筛选条件的模板">
          <Button onClick={() => setMineFilters(NO_FILTERS)}>清空筛选</Button>
        </Empty>
      ) : (
        <Row gutter={[16, 16]}>
          {shown.map((t) => (
            <Col key={t.id} xs={24} md={12} xl={8}>
              <Card
                size="small"
                title={
                  <Flex gap={8} align="center">
                    {canManage && (
                      <Checkbox
                        aria-label={`选择「${t.title}」`}
                        checked={pickedMine.has(t.id ?? '')}
                        onChange={(e) => setPickedMine((s) => toggle(s, t.id ?? '', e.target.checked))}
                      />
                    )}
                    <Typography.Text strong ellipsis>
                      {t.title}
                    </Typography.Text>
                  </Flex>
                }
                extra={t.archived ? <Tag>已归档</Tag> : <Tag>第 {t.version} 版</Tag>}
                actions={[
                  <Button key="preview" type="link" icon={<EyeOutlined />} onClick={() => setPreviewing(t)}>
                    预览
                  </Button>,
                  <Button
                    key="edit"
                    type="link"
                    icon={<EditOutlined />}
                    onClick={() => void navigate(`/templates/${t.id}`)}
                  >
                    {canManage ? '编辑' : '查看'}
                  </Button>,
                  ...(canManage
                    ? [
                        <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t, format })} />,
                        <Dropdown
                          key="more"
                          menu={{
                            items: [
                              { key: 'copy', icon: <CopyOutlined />, label: '复制一份' },
                              { key: 'labels', icon: <TagsOutlined />, label: '改标签' },
                              {
                                key: 'archive',
                                icon: <InboxOutlined />,
                                label: t.archived ? '恢复' : '归档（不再出现在上传里）',
                              },
                              { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true },
                            ],
                            onClick: ({ key }) =>
                              key === 'delete'
                                ? askDelete(t)
                                : key === 'copy'
                                  ? create.mutate({ source: 'copy', copy_of: t.id ?? '' })
                                  : key === 'labels'
                                    ? setLabelling(t)
                                    : archive.mutate({ id: t.id ?? '', archived: !t.archived }),
                          }}
                        >
                          <Button type="link" icon={<MoreOutlined />} aria-label="更多">
                            更多
                          </Button>
                        </Dropdown>,
                      ]
                    : [
                        <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t, format })} />,
                      ]),
                ]}
              >
                <Flex wrap gap={4} style={{ marginBottom: 8 }}>
                  <LabelTags template={t} />
                </Flex>
                <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ minHeight: 44 }}>
                  {t.description || '（没有说明）'}
                </Typography.Paragraph>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {counts(t)}
                  {t.updated_at ? ` · 改于 ${formatTime(t.updated_at)}` : ''}
                </Typography.Text>
                <br />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {SOURCE_TEXT[sourceKind(t.source)] ?? ''}
                  {t.used ? ` · ${t.used} 份文档用过` : ' · 还没有文档用过'}
                  {t.last_used_at ? `，最近 ${formatTime(t.last_used_at)}` : ''}
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
        添加到我的模板后可以随便改，不影响原版。
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
            onChange={(e) => setPickedStarters(new Set(e.target.checked ? startersShown.map((t) => t.name) : []))}
          >
            全选（{startersShown.length}）
          </Checkbox>
          {pickedStartersShown.length > 0 && (
            <>
              <Typography.Text type="secondary">已选 {pickedStartersShown.length} 个</Typography.Text>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                loading={addMany.isPending}
                onClick={() => askAddMany(pickedStartersShown)}
              >
                添加选中的 {pickedStartersShown.length} 个到我的模板
              </Button>
              <Button type="link" onClick={() => setPickedStarters(new Set())}>
                取消选择
              </Button>
            </>
          )}
        </Flex>
      )}
      {/* 按地区分组（ADR 0048）：通用在前，中国、澳洲，再按国家名排 */}
      {regionGroups(startersShown).map(([region, group]) => (
        <Flex key={region} vertical gap={8}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            {region ? countryName(region) : '通用（不分国家）'}
            <Typography.Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginInlineStart: 8 }}>
              {group.length} 个
            </Typography.Text>
          </Typography.Title>
          <Row gutter={[16, 16]}>
            {group.map((t) => (
              <Col key={t.name} xs={24} md={12} xl={8}>
                <Card
                  size="small"
                  title={
                    <Flex gap={8} align="center">
                      {canManage && (
                        <Checkbox
                          aria-label={`选择「${t.title}」`}
                          checked={pickedStarters.has(t.name)}
                          onChange={(e) => setPickedStarters((s) => toggle(s, t.name, e.target.checked))}
                        />
                      )}
                      <Typography.Text strong ellipsis>
                        {t.title}
                      </Typography.Text>
                    </Flex>
                  }
                  extra={added.has(`starter:${t.name}`) ? <Tag color="green">已添加</Tag> : null}
                  actions={[
                    <Button key="preview" type="link" icon={<EyeOutlined />} onClick={() => setPreviewing(t)}>
                      预览
                    </Button>,
                    <Tooltip key="add" title={canManage ? undefined : '没有"管理模板"的权限'}>
                      <Button
                        type="link"
                        icon={<PlusOutlined />}
                        disabled={!canManage}
                        loading={create.isPending && create.variables?.starter === t.name}
                        onClick={() => create.mutate({ source: 'starter', starter: t.name })}
                      >
                        添加
                      </Button>
                    </Tooltip>,
                    <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t, format })} />,
                  ]}
                >
                  <Flex wrap gap={4} style={{ marginBottom: 8 }}>
                    <LabelTags template={t} custom={false} />
                  </Flex>
                  <Typography.Paragraph type="secondary" ellipsis={{ rows: 3 }} style={{ minHeight: 66 }}>
                    {t.description}
                  </Typography.Paragraph>
                  {(t.languages?.length ?? 0) > 1 && (
                    <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
                      名称语言：
                      {(t.languages?.length ?? 0) > 4
                        ? `${(t.languages ?? []).slice(0, 2).map(languageName).join(' · ')} 等 ${t.languages?.length} 种`
                        : (t.languages ?? []).map(languageName).join(' · ')}
                    </Typography.Paragraph>
                  )}
                  <Typography.Paragraph style={{ fontSize: 12, marginBottom: 4 }} ellipsis={{ rows: 2 }}>
                    {t.fields.map((f) => f.label).join('、')}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {counts(t)}
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
      title="模板"
      extra={
        <Segmented
          value={tab}
          onChange={(value) => setParams(value === 'starters' ? { tab: 'starters' } : {})}
          options={[
            { label: `我的模板（${own.filter((t) => !t.archived).length}）`, value: 'mine' },
            { label: '起步模板库', value: 'starters' },
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
              <Button onClick={() => setPreviewing(null)}>关闭</Button>
              {previewing.kind === 'mine' ? (
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => void navigate(`/templates/${previewing.id}`)}
                >
                  {canManage ? '编辑' : '查看详情'}
                </Button>
              ) : (
                <Tooltip title={canManage ? undefined : '没有"管理模板"的权限'}>
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    disabled={!canManage}
                    loading={create.isPending}
                    onClick={() => create.mutate({ source: 'starter', starter: previewing.name })}
                  >
                    {added.has(`starter:${previewing.name}`) ? '再添加一份到我的模板' : '添加到我的模板'}
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

/** 只改标签（功能领域、国家和地区、自定义标签），不出新版本。 */
function LabelsDialog({
  template,
  onClose,
  onSaved,
}: {
  template: Template
  onClose: () => void
  onSaved: () => void
}) {
  const { message } = App.useApp()
  const [labels, setLabels] = useState({
    domains: template.domains ?? (template.category ? [template.category] : []),
    countries: template.countries ?? [],
    tags: template.tags ?? [],
  })
  const save = useMutation({
    mutationFn: () => setTemplateLabels(template.id ?? '', labels),
    onSuccess: () => {
      void message.success(`已改「${template.title}」的标签`)
      onSaved()
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  return (
    <Modal
      open
      title={`「${template.title}」的标签`}
      okText="保存"
      cancelText="取消"
      confirmLoading={save.isPending}
      onOk={() => save.mutate()}
      onCancel={onClose}
      width={640}
    >
      <Typography.Paragraph type="secondary">
        标签只用来分类、筛选，改了不出新版本，也不影响用过它的文档。
      </Typography.Paragraph>
      <LabelFields {...labels} onChange={(patch) => setLabels((l) => ({ ...l, ...patch }))} />
    </Modal>
  )
}

/** 导出模板：标准 Excel（人能看懂、能改，改完再导入）或 JSON（全部内容，连原表样式）。 */
function ExportMenu({ onExport }: { onExport: (format: 'xlsx' | 'json') => void }) {
  return (
    <Dropdown
      menu={{
        items: [
          { key: 'xlsx', label: 'Excel（能看、能改，改完再导入）' },
          { key: 'json', label: 'JSON（全部内容，连按原表导出的样式）' },
        ],
        onClick: ({ key }) => onExport(key as 'xlsx' | 'json'),
      }}
    >
      <Button type="link" icon={<DownloadOutlined />}>
        导出
      </Button>
    </Dropdown>
  )
}
