import { useQuery } from '@tanstack/react-query'
import { Empty, Flex, List, Modal, Select, Table, type TableProps, Tabs, Tag, Typography } from 'antd'
import { type ReactNode, useState } from 'react'
import { listRules } from '../../api/templates'
import type { Template, TemplateField, TemplateTable } from '../../api/types'
import { languageName, languageOptions } from '../../utils/languages'
import { countryName } from './labels'
import { LabelTags } from './LabelWidgets'
import { TYPE_OPTIONS } from './templateFields'
import { DEFAULT_LANGUAGE, textIn } from './templateLanguage'
import { useTranslation } from 'react-i18next'

/** Type name in the current interface language (the option labels are read when shown). */
function typeText(type: string): string {
  return TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type
}

/** Language shown in the preview and the template's primary language. */
interface Shown {
  language: string
  primary: string
}

/** Overview of fields (or table columns): name, type, required, aliases, description. */
function FieldTable({ fields, shown }: { fields: TemplateField[]; shown: Shown }) {
  const { t } = useTranslation()
  const { language, primary } = shown
  const columns: TableProps<TemplateField>['columns'] = [
    {
      title: t('名称'),
      key: 'label',
      width: 180,
      render: (_, f) => (
        <Flex vertical>
          <Typography.Text strong>{textIn(f.label, f.names, primary, language)}</Typography.Text>
          {language !== primary && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{f.label}</Typography.Text>}
        </Flex>
      ),
    },
    {
      title: t('类型'),
      key: 'type',
      width: 120,
      render: (_, f) => (
        <Flex vertical gap={2}>
          <span>{typeText(f.type)}</span>
          {f.compute ? <Typography.Text code style={{ fontSize: 12 }}>{f.compute}</Typography.Text> : null}
        </Flex>
      ),
    },
    {
      title: t('要求'),
      key: 'flags',
      width: 150,
      render: (_, f) => (
        <Flex gap={4} wrap>
          {f.required && <Tag color="red" bordered={false}>{t('必填')}</Tag>}
          {f.unique && <Tag color="purple" bordered={false}>{t('不能重复')}</Tag>}
          {f.sensitive && <Tag color="orange" bordered={false}>{t('敏感')}</Tag>}
          {f.extract === false && <Tag bordered={false}>{t('人工填')}</Tag>}
          {f.export === false && <Tag bordered={false}>{t('不导出')}</Tag>}
        </Flex>
      ),
    },
    {
      title: t('单据上可能写作'),
      key: 'aliases',
      render: (_, f) => (
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 2, tooltip: f.aliases.join(t('、')) }} style={{ margin: 0 }}>
          {f.aliases.join(t('、')) || '—'}
        </Typography.Paragraph>
      ),
    },
    {
      title: t('说明'),
      key: 'description',
      render: (_, f) => {
        const description = textIn(f.description, f.descriptions, primary, language)
        return (
          <Typography.Paragraph ellipsis={{ rows: 2, tooltip: description }} style={{ margin: 0 }}>
            {description || '—'}
          </Typography.Paragraph>
        )
      },
    },
  ]
  return <Table size="small" rowKey="name" columns={columns} dataSource={fields} pagination={false} />
}

function TablesPane({ tables, shown }: { tables: TemplateTable[]; shown: Shown }) {
  const { t } = useTranslation()
  if (tables.length === 0) return <Empty description={t('这个模板没有表格')} />
  return (
    <Flex vertical gap={16}>
      {tables.map((tbl) => (
        <Flex key={tbl.name} vertical gap={6}>
          <Flex gap={8} align="center" wrap>
            <Typography.Text strong>{textIn(tbl.label, tbl.names, shown.primary, shown.language)}</Typography.Text>
            <Typography.Text type="secondary">{t('{{length}} 列', { length: tbl.columns.length })}</Typography.Text>
            {tbl.required && <Tag color="red" bordered={false}>{t('至少一行')}</Tag>}
          </Flex>
          {tbl.description && (
            <Typography.Text type="secondary">
              {textIn(tbl.description, tbl.descriptions, shown.primary, shown.language)}
            </Typography.Text>
          )}
          <FieldTable fields={tbl.columns} shown={shown} />
        </Flex>
      ))}
    </Flex>
  )
}

/**
 * Template preview: shows the template's content without opening the editor (modeled on the Notion template
 * gallery and Rossum's schema preview). Name, tags, description and counts on top; tabs for fields, tables,
 * rules and basis in the middle; action buttons (supplied by the caller) at the bottom.
 */
export function TemplatePreview({
  template,
  basis,
  onClose,
  actions,
}: {
  template: Template | null
  /**
   * Basis: the starter template's own; for my templates copied from a starter template, pass that starter
   * template's basis.
   */
  basis?: string[]
  onClose: () => void
  actions?: ReactNode
}) {
  const { t } = useTranslation()
  const rules = useQuery({ queryKey: ['template-rules'], queryFn: listRules, staleTime: Infinity })
  const [language, setLanguage] = useState<string>()
  if (!template) return null
  const primary = template.language ?? DEFAULT_LANGUAGE
  const languages = template.languages?.length ? template.languages : [primary]
  // The primary language unless another one was picked.
  const shown = language && languages.includes(language) ? language : primary
  const view: Shown = { language: shown, primary }
  const country = new Map((rules.data ?? []).map((r) => [r.id, r.country]))
  const custom = template.custom_rules ?? []
  const columnCount = template.tables.reduce((n, tbl) => n + tbl.columns.length, 0)
  const notes = basis ?? template.basis ?? []
  const title = textIn(template.title, template.titles, primary, shown)
  const description = textIn(template.description, template.descriptions, primary, shown)

  return (
    <Modal
      open
      onCancel={onClose}
      width={960}
      centered
      title={
        <Flex gap={8} align="center" wrap style={{ paddingRight: 24 }}>
          <span>{title}</span>
          {shown !== primary && <Typography.Text type="secondary" style={{ fontWeight: 400 }}>{template.title}</Typography.Text>}
          {template.kind === 'mine' && <Tag>{t('第 {{version}} 版', { version: template.version })}</Tag>}
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
              <Typography.Text type="secondary">{t('名称语言')}</Typography.Text>
              <Select
                size="small"
                aria-label={t('预览的名称语言')}
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
        {description && <Typography.Paragraph style={{ margin: 0 }}>{description}</Typography.Paragraph>}
        <Typography.Text type="secondary">
          {t('{{length}} 个字段', { length: template.fields.length })}
          {template.tables.length > 0 ? t(' · {{length}} 张表格（{{columnCount}} 列）', { length: template.tables.length, columnCount }) : ''}
          {t(' · {{value}} 条规则', { value: template.rules.length + custom.length })}
          {languages.length > 1 ? t(' · 名称有 {{length}} 种语言', { length: languages.length }) : ''}
        </Typography.Text>
        <Tabs
          items={[
            {
              key: 'fields',
              label: t('字段（{{length}}）', { length: template.fields.length }),
              children: <FieldTable fields={template.fields} shown={view} />,
            },
            {
              key: 'tables',
              label: t('表格（{{length}}）', { length: template.tables.length }),
              children: <TablesPane tables={template.tables} shown={view} />,
            },
            {
              key: 'rules',
              label: t('规则（{{value}}）', { value: template.rules.length + custom.length }),
              children:
                template.rules.length + custom.length === 0 ? (
                  <Empty description={t('没有选规则')} />
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
                            {r.severity === 'error' ? t('错误') : t('警告')}
                          </Tag>
                          {r.country && <Tag color="geekblue" bordered={false}>{countryName(r.country)}</Tag>}
                          {r.custom && <Tag bordered={false}>{t('自己写的')}</Tag>}
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
                    label: t('依据'),
                    children: (
                      <Flex vertical gap={8}>
                        {template.kind === 'mine' && (
                          <Typography.Text type="secondary">{t('这份模板是从起步模板复制的，下面是起步模板的依据。')}</Typography.Text>
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
        {shown !== primary && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('名称和说明显示为{{languageName}}；没写这种语言的显示主语言（{{primaryName}}）的。', {
              languageName: languageName(shown),
              primaryName: languageName(primary),
            })}
          </Typography.Text>
        )}
      </Flex>
    </Modal>
  )
}
