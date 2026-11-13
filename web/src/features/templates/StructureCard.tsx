import { Card, Descriptions, Flex, Select, Tag, Typography } from 'antd'
import type { Template, TemplateField, TemplateTable } from '../../api/types'
import { ROLE_TEXT } from './excelLabels'

/** 字段 ID → 中文名（表格的列写成 items.amount）。 */
function labelsOf(t: Template): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const f of t.fields) labels[f.name] = f.label
  for (const table of t.tables) {
    for (const c of table.columns) labels[`${table.name}.${c.name}`] = `${table.label} · ${c.label}`
  }
  return labels
}

/** 这一格填什么，写成人话。 */
function describe(ref: string, labels: Record<string, string>, t: Template, auto: Record<string, string>) {
  if (!ref) return '（留空）'
  if (ref.startsWith('auto:')) return `自动：${auto[ref.slice(5)] ?? ref.slice(5)}`
  const [field, part] = ref.split('.')
  if (part && PARTS[part] && labels[field]) return `${labels[field]}的${PARTS[part]}`
  if (ref.startsWith('ref:')) {
    const [name, column] = ref.slice(4).split('.')
    const reference = t.references?.find((r) => r.name === name)
    return `对照「${reference?.label ?? name}」的${column}`
  }
  return labels[ref] ?? ref
}

/** 日期范围字段能填进一列的部分（ADR 0039）。 */
const PARTS: Record<string, string> = { start: '开始', end: '结束', days: '天数' }

/** 一列能选填什么：普通 sheet 选字段（日期范围还能选开始、结束、天数），明细 sheet 选这张表的列。 */
function choices(
  fields: TemplateField[],
  tables: TemplateTable[],
  table: string | null | undefined,
  current: string,
  describeRef: (ref: string) => string,
) {
  const options: { value: string; label: string }[] = [{ value: '', label: '（留空）' }]
  if (current && (current.startsWith('auto:') || current.startsWith('ref:'))) {
    options.push({ value: current, label: describeRef(current) })
  }
  const columns = tables.find((t) => t.name === table)?.columns
  if (columns) {
    options.push(...columns.map((c) => ({ value: `${table}.${c.name}`, label: c.label })))
  } else {
    for (const f of fields) {
      if (f.type === 'date_range') {
        options.push(
          { value: f.name, label: `${f.label}（统一格式）` },
          ...Object.entries(PARTS).map(([part, name]) => ({
            value: `${f.name}.${part}`,
            label: `${f.label}的${name}`,
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
  seq: '序号',
  filename: '文件名',
  page: '第几页',
  row: '明细第几行',
  uploaded_at: '上传时间',
  processed_at: '处理时间',
  reviewed_at: '审核时间',
}

/**
 * 从 Excel 建的模板：原表每张 sheet 的角色、每一列填什么，对象和参照表（ADR 0031）。
 * 每一列填什么可以改（例如把"开始日期""结束日期"两列对到一个日期范围字段的开始、结束，ADR 0039），
 * 改了随模板一起保存。sheet 的角色、对象、参照表不能改，要改就重新从 Excel 建。
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
  const shape = { ...template, fields: fields ?? template.fields, tables: tables ?? template.tables }
  const labels = labelsOf(shape)
  const editable = !readOnly && onChangeRef !== undefined
  return (
    <Card size="small" title="Excel 结构（从 Excel 建的）">
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          导出时按原表「{template.workbook?.filename ?? '—'}」填。每一列填什么可以在这里改，随模板一起保存；
          上面删掉的字段，导出时那一列留空。
        </Typography.Text>
        {template.workbook?.sheets.map((s) => (
          <Flex key={s.name} vertical gap={4}>
            <Flex gap={8} align="center" wrap>
              <Typography.Text strong>{s.name}</Typography.Text>
              <Tag color={ROLE_TEXT[s.role].color}>{ROLE_TEXT[s.role].label}</Tag>
              {s.same_as && <Typography.Text type="secondary">和「{s.same_as}」一样</Typography.Text>}
            </Flex>
            {s.columns.length > 0 && !s.same_as && !editable && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {s.columns
                  .map((c) => `${c.header} ← ${describe(columnRefs[`${s.name}::${c.header}`] ?? c.ref, labels, template, AUTO)}`)
                  .join('；')}
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
          <Descriptions size="small" column={1} title="对象（几份单据按编号合并）">
            {template.entities?.map((e) => (
              <Descriptions.Item key={e.name} label={e.label}>
                编号：{labels[e.key] ?? e.key}；包括 {e.fields.map((f) => labels[f] ?? f).join('、')}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
        {(template.references ?? []).length > 0 && (
          <Descriptions size="small" column={1} title="参照表">
            {template.references?.map((r) => (
              <Descriptions.Item key={r.name} label={r.label}>
                {r.row_count} 行，列：{r.columns.join('、')}
                {r.match_field
                  ? `；拿「${labels[r.match_field] ?? r.match_field}」到「${r.match_column}」里对照`
                  : '；不对照'}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </Flex>
    </Card>
  )
}
