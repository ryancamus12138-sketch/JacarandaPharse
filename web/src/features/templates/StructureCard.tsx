import { Card, Descriptions, Flex, Select, Tag, Typography } from 'antd'
import type { Template, TemplateField, TemplateTable } from '../../api/types'
import { ROLE_TEXT } from './excelLabels'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

/** Field ID → display name (table columns as items.amount). */
function labelsOf(tpl: Template): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const f of tpl.fields) labels[f.name] = f.label
  for (const table of tpl.tables) {
    for (const c of table.columns) labels[`${table.name}.${c.name}`] = `${table.label} · ${c.label}`
  }
  return labels
}

/** Content of this cell in plain language. */
function describe(ref: string, labels: Record<string, string>, template: Template, auto: Record<string, string>) {
  if (!ref) return t('（留空）')
  if (ref.startsWith('auto:')) return t('自动：{{value}}', { value: auto[ref.slice(5)] ?? ref.slice(5) })
  const [field, part] = ref.split('.')
  if (part && PARTS[part] && labels[field]) return t('{{value}}的{{value2}}', { value: labels[field], value2: PARTS[part] })
  if (ref.startsWith('ref:')) {
    const [name, column] = ref.slice(4).split('.')
    const reference = template.references?.find((r) => r.name === name)
    return t('对照「{{value}}」的{{column}}', { value: reference?.label ?? name, column })
  }
  return labels[ref] ?? ref
}

/** Parts of a date range field that can fill a column. */
const PARTS: Record<string, string> = { get start() { return t('开始') }, get end() { return t('结束') }, get days() { return t('天数') } }

/**
 * Choices for a column: fields for regular sheets (date ranges also offer start, end and days), this table's
 * columns for line-item sheets.
 */
function choices(
  fields: TemplateField[],
  tables: TemplateTable[],
  table: string | null | undefined,
  current: string,
  describeRef: (ref: string) => string,
) {
  const options: { value: string; label: string }[] = [{ value: '', label: t('（留空）') }]
  if (current && (current.startsWith('auto:') || current.startsWith('ref:'))) {
    options.push({ value: current, label: describeRef(current) })
  }
  const columns = tables.find((tbl) => tbl.name === table)?.columns
  if (columns) {
    options.push(...columns.map((c) => ({ value: `${table}.${c.name}`, label: c.label })))
  } else {
    for (const f of fields) {
      if (f.type === 'date_range') {
        options.push(
          { value: f.name, label: t('{{label}}（统一格式）', { label: f.label }) },
          ...Object.entries(PARTS).map(([part, name]) => ({
            value: `${f.name}.${part}`,
            label: t('{{label}}的{{name}}', { label: f.label, name }),
          })),
        )
      } else {
        options.push({ value: f.name, label: f.label })
      }
    }
  }
  return options
}

const AUTO: Record<string, string> = {
  get seq() { return t('序号') },
  get filename() { return t('文件名') },
  get page() { return t('第几页') },
  get row() { return t('明细第几行') },
  get uploaded_at() { return t('上传时间') },
  get processed_at() { return t('处理时间') },
  get reviewed_at() { return t('审核时间') },
}

/**
 * Templates created from Excel: the role of each sheet of the original workbook, the content of each column,
 * subjects and reference tables. Column contents are editable (for example mapping "start date" and "end
 * date" columns to the start and end of a date range field) and saved with the template. Sheet roles,
 * subjects and reference tables cannot be changed; create the template from Excel again for that.
 */
export function StructureCard({
  template,
  fields,
  tables,
  columnRefs = {},
  readOnly = true,
  onChangeRef,
}: {
  template: Template
  fields?: TemplateField[]
  tables?: TemplateTable[]
  columnRefs?: Record<string, string>
  readOnly?: boolean
  onChangeRef?: (key: string, ref: string) => void
}) {
  const { t } = useTranslation()
  const shape = { ...template, fields: fields ?? template.fields, tables: tables ?? template.tables }
  const labels = labelsOf(shape)
  const editable = !readOnly && onChangeRef !== undefined
  return (
    <Card size="small" title={t('Excel 结构（从 Excel 建的）')}>
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          {t('导出时按原表「{{filename}}」填。每一列填什么可以在这里改，随模板一起保存；上面删掉的字段，导出时那一列留空。', { filename: template.workbook?.filename ?? '—' })}
        </Typography.Text>
        {template.workbook?.sheets.map((s) => (
          <Flex key={s.name} vertical gap={4}>
            <Flex gap={8} align="center" wrap>
              <Typography.Text strong>{s.name}</Typography.Text>
              <Tag color={ROLE_TEXT[s.role].color}>{ROLE_TEXT[s.role].label}</Tag>
              {s.same_as && <Typography.Text type="secondary">{t('和「{{same_as}}」一样', { same_as: s.same_as })}</Typography.Text>}
            </Flex>
            {s.columns.length > 0 && !s.same_as && !editable && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {s.columns
                  .map((c) => `${c.header} ← ${describe(columnRefs[`${s.name}::${c.header}`] ?? c.ref, labels, template, AUTO)}`)
                  .join(t('；'))}
              </Typography.Text>
            )}
            {s.columns.length > 0 && !s.same_as && editable && (
              <Flex vertical gap={4}>
                {s.columns.map((c) => {
                  const key = `${s.name}::${c.header}`
                  const ref = columnRefs[key] ?? c.ref
                  return (
                    <Flex key={key} gap={8} align="center">
                      <Typography.Text style={{ width: 160, fontSize: 12 }} ellipsis>
                        {c.header}
                      </Typography.Text>
                      <Typography.Text type="secondary">←</Typography.Text>
                      <Select
                        size="small"
                        style={{ minWidth: 220 }}
                        value={ref}
                        showSearch={{ optionFilterProp: 'label' }}
                        options={choices(shape.fields, shape.tables, s.table, c.ref, (r) =>
                          describe(r, labels, template, AUTO),
                        )}
                        onChange={(value: string) => onChangeRef?.(key, value)}
                      />
                    </Flex>
                  )
                })}
              </Flex>
            )}
          </Flex>
        ))}
        {(template.entities ?? []).length > 0 && (
          <Descriptions size="small" column={1} title={t('对象（几份单据按编号合并）')}>
            {template.entities?.map((e) => (
              <Descriptions.Item key={e.name} label={e.label}>
                {t('编号：{{key}}；包括 {{fields}}', { key: labels[e.key] ?? e.key, fields: e.fields.map((f) => labels[f] ?? f).join(t('、')) })}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
        {(template.references ?? []).length > 0 && (
          <Descriptions size="small" column={1} title={t('参照表')}>
            {template.references?.map((r) => (
              <Descriptions.Item key={r.name} label={r.label}>
                {t('{{row_count}} 行，列：', { row_count: r.row_count })}{r.columns.join(t('、'))}
                {r.match_field
                  ? t('；拿「{{value}}」到「{{match_column}}」里对照', { value: labels[r.match_field] ?? r.match_field, match_column: r.match_column })
                  : t('；不对照')}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </Flex>
    </Card>
  )
}
