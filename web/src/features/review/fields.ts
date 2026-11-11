/** 字段的中文名和状态说明。后端只给英文字段名，显示给人看的名字放在这里。 */

import type { Evidence, FieldValue } from '../../api/types'

const LABELS: Record<string, string> = {
  merchant: '商家（公司）',
  store_name: '店名',
  document_no: '单号',
  date: '日期',
  total: '总额',
  company_reg_no: '公司注册号',
  address: '地址',
  time: '时间',
  cashier: '收银员',
  member: '会员',
  subtotal: '小计',
  tax: '税额',
  rounding: '四舍五入',
  cash: '现金',
  change: '找零',
}

/** 表格的中文名和列的中文名。 */
const TABLE_LABELS: Record<string, string> = { items: '商品明细' }
const COLUMN_LABELS: Record<string, string> = {
  name: '商品',
  quantity: '数量',
  unit_price: '单价',
  amount: '金额',
}

/** 表格里的一格：items.2.amount = 商品明细第 2 行的金额。 */
export interface CellName {
  table: string
  index: number
  column: string
}

/** 字段名是不是表格里的一格；普通字段返回 null。和后端的 split_cell_name 一样。 */
export function splitCell(name: string): CellName | null {
  const m = /^([a-z][a-z0-9_]*)\.(\d+)\.([a-z][a-z0-9_]*)$/.exec(name)
  return m ? { table: m[1], index: Number(m[2]), column: m[3] } : null
}

export function tableLabel(table: string): string {
  return TABLE_LABELS[table] ?? table
}

export function columnLabel(column: string): string {
  return COLUMN_LABELS[column] ?? column
}

/** 字段的中文名；没登记的就显示英文原名。表格的格子显示成"第 2 行 金额"。 */
export function fieldLabel(name: string): string {
  const cell = splitCell(name)
  if (cell) {
    return `第 ${cell.index} 行 ${columnLabel(cell.column)}`
  }
  return LABELS[name] ?? name
}

/** 出处框的颜色：等人确认的黄色，审核过的绿色，其他紫色。 */
export function boxColor(value: FieldValue): string {
  if (value.pending_review) {
    return '#faad14'
  }
  if (value.reviewed) {
    return '#52c41a'
  }
  return '#7b5cc4'
}

/** 表格的一行：真实编号（字段名里的数字，接口用）和显示编号（页面上的第几行，删掉的行没有）。 */
export interface TableRow {
  index: number
  /** 页面上显示的第几行；删掉的行是 null。和后端规则说的"第几行"一致。 */
  display: number | null
  deleted: boolean
  manual: boolean
  cells: Map<string, FieldValue>
}

export interface TableLayout {
  table: string
  columns: string[]
  rows: TableRow[]
}

/**
 * 把字段里的表格格子排成表：行按后端给的顺序（显示顺序），列按出现的顺序。
 * 删掉的行留在原位（灰色、可以恢复），但不占编号。
 */
export function tableLayouts(values: FieldValue[]): TableLayout[] {
  const tables = new Map<string, { columns: string[]; rows: Map<number, TableRow> }>()
  for (const v of values) {
    const cell = splitCell(v.name)
    if (!cell) {
      continue
    }
    let t = tables.get(cell.table)
    if (!t) {
      t = { columns: [], rows: new Map() }
      tables.set(cell.table, t)
    }
    if (!t.columns.includes(cell.column)) {
      t.columns.push(cell.column)
    }
    let row = t.rows.get(cell.index)
    if (!row) {
      row = { index: cell.index, display: null, deleted: true, manual: false, cells: new Map() }
      t.rows.set(cell.index, row)
    }
    row.cells.set(cell.column, v)
    row.deleted = row.deleted && v.deleted
    row.manual = row.manual || v.origin === 'manual'
  }
  return [...tables].map(([table, t]) => {
    const rows = [...t.rows.values()]
    let n = 0
    for (const row of rows) {
      row.display = row.deleted ? null : ++n
    }
    return { table, columns: t.columns, rows }
  })
}

/** 一格的名字，按页面上的行号："第 2 行 金额"。删掉的行写"已删除的行"。 */
export function cellLabel(row: TableRow, column: string): string {
  const where = row.display === null ? '已删除的行' : `第 ${row.display} 行`
  return `${where} ${columnLabel(column)}`
}

/** 字段现在的出处：从原文选取过就是选的那几块。 */
export function evidenceOf(v: FieldValue): Evidence | null {
  return v.current_evidence ?? v.evidence
}

/** 页面上显示的名字：普通字段是中文名，表格的格子按页面上的行号（"第 2 行 商品"）。 */
export function displayLabel(values: FieldValue[], v: FieldValue): string {
  const cell = splitCell(v.name)
  if (!cell) {
    return fieldLabel(v.name)
  }
  const layout = tableLayouts(values).find((t) => t.table === cell.table)
  const row = layout?.rows.find((r) => r.index === cell.index)
  return row ? cellLabel(row, cell.column) : fieldLabel(v.name)
}

/**
 * 键盘上下左右切换到哪个字段。
 * - 普通字段：↑↓ 一个一个走；最后一个再往下，进入表格第一行第一格。
 * - 表格的格子：↑↓ 是上一行、下一行的同一列（跳过删掉的行），第一行再往上回到普通字段；
 *   ←→ 是同一行的左右一格。
 * 走不动（已经到头）返回 null。
 */
export function neighbor(
  values: FieldValue[],
  currentId: string | null,
  key: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight',
): string | null {
  const plain = values.filter((v) => splitCell(v.name) === null && !v.deleted)
  const tables = tableLayouts(values).map((t) => ({
    ...t,
    rows: t.rows.filter((r) => !r.deleted),
  }))
  const firstCell = (t: (typeof tables)[number] | undefined) =>
    t?.rows[0]?.cells.get(t.columns[0])?.id ?? null
  const current = values.find((v) => v.id === currentId)
  if (!current) {
    return key === 'ArrowDown' ? (plain[0]?.id ?? firstCell(tables[0])) : null
  }
  const cell = splitCell(current.name)
  if (!cell) {
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      return null
    }
    const i = plain.findIndex((v) => v.id === current.id)
    if (key === 'ArrowUp') {
      return i > 0 ? plain[i - 1].id : null
    }
    return i < plain.length - 1 ? plain[i + 1].id : firstCell(tables[0])
  }
  const ti = tables.findIndex((t) => t.table === cell.table)
  const t = tables[ti]
  const ri = t.rows.findIndex((r) => r.index === cell.index)
  const ci = t.columns.indexOf(cell.column)
  const at = (r: number, c: number) => t.rows[r]?.cells.get(t.columns[c])?.id ?? null
  if (key === 'ArrowLeft') {
    return ci > 0 ? at(ri, ci - 1) : null
  }
  if (key === 'ArrowRight') {
    return ci < t.columns.length - 1 ? at(ri, ci + 1) : null
  }
  if (key === 'ArrowUp') {
    if (ri > 0) {
      return at(ri - 1, ci)
    }
    return ti > 0 ? firstCell(tables[ti - 1]) : (plain[plain.length - 1]?.id ?? null)
  }
  if (ri < t.rows.length - 1) {
    return at(ri + 1, ci)
  }
  return firstCell(tables[ti + 1])
}
