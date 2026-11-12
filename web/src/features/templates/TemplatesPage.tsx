import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  InboxOutlined,
  MoreOutlined,
  PlusOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Dropdown,
  Empty,
  Flex,
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
} from '../../api/templates'
import type { Template } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'

const CATEGORY_COLORS: Record<string, string> = {
  发票: 'purple',
  交通: 'blue',
  收据: 'green',
  住宿: 'orange',
}

function CategoryTag({ category }: { category: string }) {
  return <Tag color={CATEGORY_COLORS[category] ?? 'default'}>{category || '其他'}</Tag>
}

/** 字段、表格、规则各有几个（卡片上的一行小字）。 */
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

  function askDelete(t: Template) {
    modal.confirm({
      title: `删除「${t.title}」？`,
      content:
        '模板和它的全部版本都会删掉，不能恢复。有文档用过的模板不能删，只能归档。删除前可以先导出备份。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => remove.mutateAsync(t),
    })
  }

  const own = (mine.data ?? []).filter((t) => t.kind === 'mine')
  const shown = own.filter((t) => showArchived || !t.archived)
  const archivedCount = own.filter((t) => t.archived).length
  const added = new Set(own.map((t) => t.source))

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
      <Alert
        type="info"
        showIcon
        title="也可以用样例建模板"
        description='公司有固定要交的 Excel（报销表、登记表），点"从 Excel 建"选它，按它的样子建模板，导出时直接填回它。也可以上传时选"抽取所有键值对"，处理完在审核页面把需要的字段存成模板。'
      />
      {shown.length === 0 ? (
        <Empty description="还没有自己的模板。从起步模板库添加一个，或者新建空白模板" />
      ) : (
        <Row gutter={[16, 16]}>
          {shown.map((t) => (
            <Col key={t.id} xs={24} md={12} xl={8}>
              <Card
                size="small"
                title={
                  <Flex gap={8} align="center">
                    <CategoryTag category={t.category} />
                    <Typography.Text strong ellipsis>
                      {t.title}
                    </Typography.Text>
                  </Flex>
                }
                extra={t.archived ? <Tag>已归档</Tag> : <Tag>第 {t.version} 版</Tag>}
                actions={[
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
                        <Button
                          key="copy"
                          type="link"
                          icon={<CopyOutlined />}
                          onClick={() => create.mutate({ source: 'copy', copy_of: t.id ?? '' })}
                        >
                          复制
                        </Button>,
                        <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t, format })} />,
                        <Dropdown
                          key="more"
                          menu={{
                            items: [
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
                <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ minHeight: 44 }}>
                  {t.description || '（没有说明）'}
                </Typography.Paragraph>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {counts(t)}
                  {t.updated_at ? ` · 改于 ${formatTime(t.updated_at)}` : ''}
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
        起步模板的字段按官方规定整理（国家税务总局公告、ATO、EN 16931 等，见每个模板的说明）。
        添加到我的模板之后可以随便改，不影响这里的原版。
      </Typography.Text>
      <Row gutter={[16, 16]}>
        {(starters.data ?? []).map((t) => (
          <Col key={t.name} xs={24} md={12} xl={8}>
            <Card
              size="small"
              title={
                <Flex gap={8} align="center">
                  <CategoryTag category={t.category} />
                  <Typography.Text strong ellipsis>
                    {t.title}
                  </Typography.Text>
                </Flex>
              }
              extra={added.has(`starter:${t.name}`) ? <Tag color="green">已添加</Tag> : null}
              actions={[
                <Tooltip key="add" title={canManage ? undefined : '没有"管理模板"的权限'}>
                  <Button
                    type="link"
                    icon={<PlusOutlined />}
                    disabled={!canManage}
                    loading={create.isPending && create.variables?.starter === t.name}
                    onClick={() => create.mutate({ source: 'starter', starter: t.name })}
                  >
                    添加到我的模板
                  </Button>
                </Tooltip>,
                <ExportMenu key="export" onExport={(format) => exportOne.mutate({ t, format })} />,
              ]}
            >
              <Typography.Paragraph type="secondary" ellipsis={{ rows: 3 }} style={{ minHeight: 66 }}>
                {t.description}
              </Typography.Paragraph>
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
    </Card>
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
