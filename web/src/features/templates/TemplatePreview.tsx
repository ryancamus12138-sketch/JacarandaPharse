import { useQuery } from '@tanstack/react-query'
import { Empty, Flex, List, Modal, Select, Table, type TableProps, Tabs, Tag, Typography } from 'antd'
import { type ReactNode, useState } from 'react'
import { listRules } from '../../api/templates'
import type { Template, TemplateField, TemplateTable } from '../../api/types'
import { languageName, languageOptions } from '../../utils/languages'
import { countryName } from './labels'
import { LabelTags } from './LabelWidgets'
import { TYPE_OPTIONS } from './templateFields'

const TYPE_TEXT = Object.fromEntries(TYPE_OPTIONS.map((o) => [o.value, o.label]))

function nameIn(label: string, names: Record<string, string> | undefined, language: string): string {
  return language === 'zh' ? label : (names?.[language] ?? label)
}

/** 字段（或表格的列）一览：名称、类型、必填、别名、说明。 */
function FieldTable({ fields, language }: { fields: TemplateField[]; language: string }) {
  const columns: TableProps<TemplateField>['columns'] = [
    {
      title: '名称',
      key: 'label',
      width: 180,
      render: (_, f) => (
        <Flex vertical>
          <Typography.Text strong>{nameIn(f.label, f.names, language)}</Typography.Text>
          {language !== 'zh' && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{f.label}</Typography.Text>}
        </Flex>
      ),
    },
    {
      title: '类型',
      key: 'type',
      width: 120,
      render: (_, f) => (
        <Flex vertical gap={2}>
          <span>{TYPE_TEXT[f.type] ?? f.type}</span>
          {f.compute ? <Typography.Text code style={{ fontSize: 12 }}>{f.compute}</Typography.Text> : null}
        </Flex>
      ),
    },
    {
      title: '要求',
      key: 'flags',
      width: 150,
      render: (_, f) => (
        <Flex gap={4} wrap>
          {f.required && <Tag color="red" bordered={false}>必填</Tag>}
          {f.unique && <Tag color="purple" bordered={false}>不能重复</Tag>}
          {f.sensitive && <Tag color="orange" bordered={false}>敏感</Tag>}
          {f.extract === false && <Tag bordered={false}>人工填</Tag>}
          {f.export === false && <Tag bordered={false}>不导出</Tag>}
        </Flex>
      ),
    },
    {
      title: '单据上可能写作',
      key: 'aliases',
      render: (_, f) => (
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 2, tooltip: f.aliases.join('、') }} style={{ margin: 0 }}>
          {f.aliases.join('、') || '—'}
        </Typography.Paragraph>
      ),
    },
    {
      title: '说明',
      key: 'description',
      render: (_, f) => (
        <Typography.Paragraph ellipsis={{ rows: 2, tooltip: f.description }} style={{ margin: 0 }}>
          {f.description || '—'}
        </Typography.Paragraph>
      ),
    },
  ]
  return <Table size="small" rowKey="name" columns={columns} dataSource={fields} pagination={false} />
}

function TablesPane({ tables, language }: { tables: TemplateTable[]; language: string }) {
  if (tables.length === 0) return <Empty description="这个模板没有表格" />
  return (
    <Flex vertical gap={16}>
      {tables.map((t) => (
        <Flex key={t.name} vertical gap={6}>
          <Flex gap={8} align="center" wrap>
            <Typography.Text strong>{nameIn(t.label, t.names, language)}</Typography.Text>
            <Typography.Text type="secondary">{t.columns.length} 列</Typography.Text>
            {t.required && <Tag color="red" bordered={false}>至少一行</Tag>}
          </Flex>
          {t.description && <Typography.Text type="secondary">{t.description}</Typography.Text>}
          <FieldTable fields={t.columns} language={language} />
        </Flex>
      ))}
    </Flex>
  )
}

/**
 * 模板预览：不用打开编辑页就能看清模板里有什么（参考 Notion 模板库、Rossum 的字段结构预览）。
 * 上面是名称、标签、说明、数字；中间分页看字段、表格、规则、依据；下面是操作按钮（由调用方给）。
 */
export function TemplatePreview({
  template,
  basis,
  onClose,
  actions,
}: {
  template: Template | null
  /** 依据：起步模板自己的；从起步模板复制的我的模板，传它的起步模板的。 */
  basis?: string[]
  onClose: () => void
  actions?: ReactNode
}) {
  const rules = useQuery({ queryKey: ['template-rules'], queryFn: listRules, staleTime: Infinity })
  const [language, setLanguage] = useState('zh')
  if (!template) return null
  const languages = template.languages?.length ? template.languages : ['zh']
  const shown = languages.includes(language) ? language : 'zh'
  const country = new Map((rules.data ?? []).map((r) => [r.id, r.country]))
  const custom = template.custom_rules ?? []
  const columnCount = template.tables.reduce((n, t) => n + t.columns.length, 0)
  const notes = basis ?? template.basis ?? []
  const title = nameIn(template.title, template.titles, shown)

  return (
    <Modal
      open
      onCancel={onClose}
      width={960}
      centered
      title={
        <Flex gap={8} align="center" wrap style={{ paddingRight: 24 }}>
          <span>{title}</span>
          {shown !== 'zh' && <Typography.Text type="secondary" style={{ fontWeight: 400 }}>{template.title}</Typography.Text>}
          {template.kind === 'mine' && <Tag>第 {template.version} 版</Tag>}
        </Flex>
      }
      footer={actions}
      styles={{ body: { height: 'min(620px, 68vh)', overflowY: 'auto', paddingRight: 8 } }}
    >
      <Flex vertical gap={12}>
        <Flex gap={8} wrap align="center" justify="space-between">
          <Flex gap={4} wrap>
            <LabelTags template={template} custom={template.kind === 'mine'} />
          </Flex>
          {languages.length > 1 && (
            <Flex gap={8} align="center">
              <Typography.Text type="secondary">名称语言</Typography.Text>
              <Select
                size="small"
                aria-label="预览的名称语言"
                value={shown}
                onChange={setLanguage}
                options={languageOptions(languages)}
                style={{ minWidth: 140 }}
                showSearch
                optionFilterProp="label"
              />
            </Flex>
          )}
        </Flex>
        {template.description && <Typography.Paragraph style={{ margin: 0 }}>{template.description}</Typography.Paragraph>}
        <Typography.Text type="secondary">
          {template.fields.length} 个字段
          {template.tables.length > 0 ? ` · ${template.tables.length} 张表格（${columnCount} 列）` : ''}
          {` · ${template.rules.length + custom.length} 条规则`}
          {languages.length > 1 ? ` · 名称有 ${languages.length} 种语言` : ''}
        </Typography.Text>
        <Tabs
          items={[
            {
              key: 'fields',
              label: `字段（${template.fields.length}）`,
              children: <FieldTable fields={template.fields} language={shown} />,
            },
            {
              key: 'tables',
              label: `表格（${template.tables.length}）`,
              children: <TablesPane tables={template.tables} language={shown} />,
            },
            {
              key: 'rules',
              label: `规则（${template.rules.length + custom.length}）`,
              children:
                template.rules.length + custom.length === 0 ? (
                  <Empty description="没有选规则" />
                ) : (
                  <List
                    size="small"
                    dataSource={[
                      ...template.rules.map((r) => ({ key: r.id, title: r.title, severity: r.severity, country: country.get(r.id) ?? '', custom: false })),
                      ...custom.map((c, i) => ({ key: `custom_${i}`, title: c.title || c.check, severity: c.severity, country: '', custom: true })),
                    ]}
                    renderItem={(r) => (
                      <List.Item>
                        <Flex gap={8} align="center" wrap>
                          <Typography.Text>{r.title}</Typography.Text>
                          <Tag color={r.severity === 'error' ? 'red' : 'gold'} bordered={false}>
                            {r.severity === 'error' ? '错误' : '警告'}
                          </Tag>
                          {r.country && <Tag color="geekblue" bordered={false}>{countryName(r.country)}</Tag>}
                          {r.custom && <Tag bordered={false}>自己写的</Tag>}
                        </Flex>
                      </List.Item>
                    )}
                  />
                ),
            },
            ...(notes.length > 0
              ? [
                  {
                    key: 'basis',
                    label: '依据',
                    children: (
                      <Flex vertical gap={8}>
                        {template.kind === 'mine' && (
                          <Typography.Text type="secondary">这份模板是从起步模板复制的，下面是起步模板的依据。</Typography.Text>
                        )}
                        {notes.map((n, i) => (
                          <Typography.Paragraph key={i} style={{ margin: 0 }}>
                            {n}
                          </Typography.Paragraph>
                        ))}
                      </Flex>
                    ),
                  },
                ]
              : []),
          ]}
        />
        {shown !== 'zh' && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            名称显示为{languageName(shown)}；没写这种语言的显示中文名。
          </Typography.Text>
        )}
      </Flex>
    </Modal>
  )
}
