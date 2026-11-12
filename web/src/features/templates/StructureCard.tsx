import { Card, Descriptions, Flex, Tag, Typography } from 'antd'
import type { Template } from '../../api/types'
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
  if (ref.startsWith('ref:')) {
    const [name, column] = ref.slice(4).split('.')
    const reference = t.references?.find((r) => r.name === name)
    return `对照「${reference?.label ?? name}」的${column}`
  }
  return labels[ref] ?? ref
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
 * 这些在编辑器里不能改：要改结构就重新从 Excel 建。字段的名字、说明、必填这些照常在上面改。
 */
export function StructureCard({ template }: { template: Template }) {
  const labels = labelsOf(template)
  return (
    <Card size="small" title="Excel 结构（从 Excel 建的）">
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          导出时按原表「{template.workbook?.filename ?? '—'}」填。这里的结构不能改，要改请重新从 Excel 建；
          上面删掉的字段，导出时那一列留空。
        </Typography.Text>
        {template.workbook?.sheets.map((s) => (
          <Flex key={s.name} vertical gap={4}>
            <Flex gap={8} align="center" wrap>
              <Typography.Text strong>{s.name}</Typography.Text>
              <Tag color={ROLE_TEXT[s.role].color}>{ROLE_TEXT[s.role].label}</Tag>
              {s.same_as && <Typography.Text type="secondary">和「{s.same_as}」一样</Typography.Text>}
            </Flex>
            {s.columns.length > 0 && !s.same_as && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {s.columns
                  .map((c) => `${c.header} ← ${describe(c.ref, labels, template, AUTO)}`)
                  .join('；')}
              </Typography.Text>
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
