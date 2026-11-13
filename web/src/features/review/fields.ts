/**
 * 字段的中文名和状态说明。
 * 中文名优先用模板里的（后端随文档详情一起给，见 setTemplateLabels）；
 * 老文档、内置模板没有的，用下面这张表。
 */

import type { CheckState, Evidence, EvidenceState, FieldValue, Level } from '../../api/types'

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

/** 现在打开的文档用的模板里的中文名：字段 ID → 名字；表格的列写成 items.amount。 */
let templateLabels: Record<string, string> = {}

/**
 * 换成这份文档的模板里的中文名。审核页面每次渲染时调用（同一份就是同一个对象，不重复处理）。
 * 一个页面同时只看一份文档，所以放在模块里，各个组件直接用 fieldLabel 就行。
 */
export function setTemplateLabels(labels: Record<string, string> | undefined): void {
  templateLabels = labels ?? {}
}

export function tableLabel(table: string): string {
  return templateLabels[table] ?? TABLE_LABELS[table] ?? table
}

export function columnLabel(column: string, table = 'items'): string {
  return templateLabels[`${table}.${column}`] ?? COLUMN_LABELS[column] ?? column
}

/** 字段的中文名；没登记的就显示英文原名。表格的格子显示成"第 2 行 金额"。 */
export function fieldLabel(name: string): string {
  const cell = splitCell(name)
  if (cell) {
    return `第 ${cell.index} 行 ${columnLabel(cell.column, cell.table)}`
  }
  return templateLabels[name] ?? LABELS[name] ?? name
}

/** 四种颜色：红 = 先看，黄 = 要看一眼，灰 = 原文没有，绿 = 没问题（ADR 0028）。 */
export const LEVEL_COLORS: Record<Level, string> = {
  red: '#ff4d4f',
  yellow: '#faad14',
  grey: '#bfbfbf',
  green: '#52c41a',
}

/**
 * 显示用的值：数字存的是 118.0 这种写法（读成数再存），整数去掉多余的 .0，显示成 118；
 * 其他照原样。只影响显示，存的值和导出（Excel 是数字、CSV 本来就不带 .0）不变。
 */
export function shownValue(v: Pick<FieldValue, 'type' | 'current_value'>): string | null {
  const value = v.current_value
  if (value !== null && v.type === 'number' && /^-?\d+\.0+$/.test(value)) {
    return value.replace(/\.0+$/, '')
  }
  return value
}

/** 字段的颜色。后端算好的（证据 × 校验 × 有没有人审核过）；老接口没有时按"等不等人确认"。 */
export function levelOf(v: FieldValue): Level {
  if (v.level) {
    return v.level
  }
  return v.pending_review ? 'yellow' : v.current_value === null ? 'grey' : 'green'
}

/** 出处框、左边色条的颜色。 */
export function boxColor(value: FieldValue): string {
  return LEVEL_COLORS[levelOf(value)]
}

export const EVIDENCE_TEXT: Record<EvidenceState, string> = {
  verified: '原文一致',
  pending: '出处有疑点',
  missing: '原文里找不到',
  empty: '原文没有',
}

export const CHECK_TEXT: Record<CheckState, string> = {
  ok: '规则通过',
  warn: '规则有疑问',
  fail: '规则不通过',
}

/** 字段怎么排：按单据上的顺序，或者按状态（先红、再黄、再灰、最后绿）。 */
export type SortMode = 'document' | 'status'

/**
 * 按状态排序时的先后。只看证据和校验、不看审核过没有：确认完一个字段它会变绿，
 * 但不会突然跳到最下面，审核员 ↑↓ 走的时候位置不乱。
 */
function rank(v: FieldValue): number {
  if (v.check_state === 'fail' || v.evidence_state === 'missing') {
    return 0
  }
  if (v.check_state === 'warn' || v.evidence_state === 'pending') {
    return 1
  }
  return v.evidence_state === 'empty' ? 2 : 3
}

/**
 * 字段的先后顺序。只排普通字段；表格的格子留在原来的顺序（表格里的行不重新排）。
 * 列表、键盘 ↑↓、"下一个待确认"都按这个顺序。
 */
export function orderFields(values: FieldValue[], mode: SortMode): FieldValue[] {
  if (mode === 'document') {
    return values
  }
  const plain = values.filter((v) => splitCell(v.name) === null)
  const cells = values.filter((v) => splitCell(v.name) !== null)
  // sort 是稳定的：同一种颜色里还是单据上的顺序
  return [...[...plain].sort((a, b) => rank(a) - rank(b)), ...cells]
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
export function cellLabel(row: TableRow, column: string, table = 'items'): string {
  const where = row.display === null ? '已删除的行' : `第 ${row.display} 行`
  return `${where} ${columnLabel(column, table)}`
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
  return row ? cellLabel(row, cell.column, cell.table) : fieldLabel(v.name)
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
