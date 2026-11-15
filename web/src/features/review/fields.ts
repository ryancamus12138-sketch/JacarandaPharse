/**
 * Field display names and status descriptions.
 * Display names come from the template first (sent by the backend with the document details; see
 * setTemplateLabels); the table below covers older documents and built-in templates without them.
 */

import type { CheckState, Evidence, EvidenceState, FieldValue, Level } from '../../api/types'
import { t } from '../../i18n'

/** Fallback field names follow the template field-name language, not the interface language. */
const LABELS: Record<string, string> = {
  merchant: '商家（公司）', // i18n-ignore: field name (template content)
  store_name: '店名', // i18n-ignore: field name (template content)
  document_no: '单号', // i18n-ignore: field name (template content)
  date: '日期', // i18n-ignore: field name (template content)
  total: '总额', // i18n-ignore: field name (template content)
  company_reg_no: '公司注册号', // i18n-ignore: field name (template content)
  address: '地址', // i18n-ignore: field name (template content)
  time: '时间', // i18n-ignore: field name (template content)
  cashier: '收银员', // i18n-ignore: field name (template content)
  member: '会员', // i18n-ignore: field name (template content)
  subtotal: '小计', // i18n-ignore: field name (template content)
  tax: '税额', // i18n-ignore: field name (template content)
  rounding: '四舍五入', // i18n-ignore: field name (template content)
  cash: '现金', // i18n-ignore: field name (template content)
  change: '找零', // i18n-ignore: field name (template content)
}

/** Display names of tables and their columns. */
const TABLE_LABELS: Record<string, string> = { items: '商品明细' } // i18n-ignore: field name (template content)
const COLUMN_LABELS: Record<string, string> = {
  name: '商品', // i18n-ignore: field name (template content)
  quantity: '数量', // i18n-ignore: field name (template content)
  unit_price: '单价', // i18n-ignore: field name (template content)
  amount: '金额', // i18n-ignore: field name (template content)
}

/** A table cell: items.2.amount = amount in row 2 of the line items. */
export interface CellName {
  table: string
  index: number
  column: string
}

/** Whether the field name is a table cell; null for regular fields. Same as the backend split_cell_name. */
export function splitCell(name: string): CellName | null {
  const m = /^([a-z][a-z0-9_]*)\.(\d+)\.([a-z][a-z0-9_]*)$/.exec(name)
  return m ? { table: m[1], index: Number(m[2]), column: m[3] } : null
}

/** Display names from the template of the open document: field ID → name; table columns as items.amount. */
let templateLabels: Record<string, string> = {}

/**
 * Switch to the display names of this document's template. Called on every render of the review page (the
 * same object is not processed twice). Only one document is open per page, so the names live in this module
 * and components call fieldLabel directly.
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

/** Display name of a field; the raw ID when not registered. Table cells are shown as "Row 2 Amount". */
export function fieldLabel(name: string): string {
  const cell = splitCell(name)
  if (cell) {
    return t('第 {{index}} 行 {{columnLabel}}', { index: cell.index, columnLabel: columnLabel(cell.column, cell.table) })
  }
  return templateLabels[name] ?? LABELS[name] ?? name
}

/** Four colors: red = look first, yellow = take a look, grey = absent from the source, green = fine. */
export const LEVEL_COLORS: Record<Level, string> = {
  red: '#ff4d4f',
  yellow: '#faad14',
  grey: '#bfbfbf',
  green: '#52c41a',
}

/**
 * Display value: numbers are stored as 118.0 (parsed and stored), so integers drop the trailing .0 and
 * display as 118; everything else unchanged. Affects display only; stored values and exports (Excel as
 * numbers, CSV without .0) are unchanged.
 */
export function shownValue(v: Pick<FieldValue, 'type' | 'current_value'>): string | null {
  const value = v.current_value
  if (value !== null && v.type === 'number' && /^-?\d+\.0+$/.test(value)) {
    return value.replace(/\.0+$/, '')
  }
  return value
}

/**
 * Field color, computed by the backend (evidence × validation × reviewed); older APIs fall back to whether
 * confirmation is pending.
 */
export function levelOf(v: FieldValue): Level {
  if (v.level) {
    return v.level
  }
  return v.pending_review ? 'yellow' : v.current_value === null ? 'grey' : 'green'
}

/** Color of evidence boxes and the left color bar. */
export function boxColor(value: FieldValue): string {
  return LEVEL_COLORS[levelOf(value)]
}

export const EVIDENCE_TEXT: Record<EvidenceState, string> = {
  get verified() { return t('原文一致') },
  get pending() { return t('出处存疑') },
  get missing() { return t('原文中未找到') },
  get empty() { return t('原文缺失') },
}

export const CHECK_TEXT: Record<CheckState, string> = {
  get ok() { return t('规则通过') },
  get warn() { return t('规则存疑') },
  get fail() { return t('规则不通过') },
}

/** Field order: document order, or by status (red, yellow, grey, then green). */
export type SortMode = 'document' | 'status'

/**
 * Status order used for sorting. Based on evidence and validation only, not on review: a confirmed field
 * turns green but does not jump to the bottom, so positions stay stable while reviewers move with ↑↓.
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
 * Field order. Only regular fields are sorted; table cells keep their order (table rows are not reordered).
 * The list, ↑↓ and "next awaiting confirmation" all follow it.
 */
export function orderFields(values: FieldValue[], mode: SortMode): FieldValue[] {
  if (mode === 'document') {
    return values
  }
  const plain = values.filter((v) => splitCell(v.name) === null)
  const cells = values.filter((v) => splitCell(v.name) !== null)
  // sort is stable: within a color, document order is kept.
  return [...[...plain].sort((a, b) => rank(a) - rank(b)), ...cells]
}

/**
 * A table row: real number (the number in field names, used by the API) and display number (row number on the
 * page; absent for deleted rows).
 */
export interface TableRow {
  index: number
  /**
   * Row number displayed on the page; null for deleted rows. Matches the row numbers in backend rule
   * messages.
   */
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
 * Arrange table cells from the fields into a table: rows in the backend's order (display order), columns in
 * order of appearance. Deleted rows stay in place (grey, restorable) without a number.
 */
export function tableLayouts(values: FieldValue[]): TableLayout[] {
  const tables = new Map<string, { columns: string[]; rows: Map<number, TableRow> }>()
  for (const v of values) {
    const cell = splitCell(v.name)
    if (!cell) {
      continue
    }
    let tbl = tables.get(cell.table)
    if (!tbl) {
      tbl = { columns: [], rows: new Map() }
      tables.set(cell.table, tbl)
    }
    if (!tbl.columns.includes(cell.column)) {
      tbl.columns.push(cell.column)
    }
    let row = tbl.rows.get(cell.index)
    if (!row) {
      row = { index: cell.index, display: null, deleted: true, manual: false, cells: new Map() }
      tbl.rows.set(cell.index, row)
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

/** Name of a cell by its displayed row number: "Row 2 Amount". Deleted rows read "deleted row". */
export function cellLabel(row: TableRow, column: string, table = 'items'): string {
  const where = row.display === null ? t('已删除的行') : t('第 {{display}} 行', { display: row.display })
  return `${where} ${columnLabel(column, table)}`
}

/** Current evidence of a field: the selected blocks if selected from source text. */
export function evidenceOf(v: FieldValue): Evidence | null {
  return v.current_evidence ?? v.evidence
}

/**
 * Name shown on the page: display names for regular fields; table cells by displayed row number ("Row 2
 * Item").
 */
export function displayLabel(values: FieldValue[], v: FieldValue): string {
  const cell = splitCell(v.name)
  if (!cell) {
    return fieldLabel(v.name)
  }
  const layout = tableLayouts(values).find((tbl) => tbl.table === cell.table)
  const row = layout?.rows.find((r) => r.index === cell.index)
  return row ? cellLabel(row, cell.column, cell.table) : fieldLabel(v.name)
}

/**
 * Field reached by the arrow keys.
 * - Regular fields: ↑↓ move one by one; moving down from the last enters the first cell of the table.
 * - Table cells: ↑↓ move to the same column in the previous or next row (skipping deleted rows), and up from
 *   the first row returns to regular fields; ←→ move within the row.
 * Returns null at a boundary.
 */
export function neighbor(
  values: FieldValue[],
  currentId: string | null,
  key: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight',
): string | null {
  const plain = values.filter((v) => splitCell(v.name) === null && !v.deleted)
  const tables = tableLayouts(values).map((tbl) => ({
    ...tbl,
    rows: tbl.rows.filter((r) => !r.deleted),
  }))
  const firstCell = (tbl: (typeof tables)[number] | undefined) =>
    tbl?.rows[0]?.cells.get(tbl.columns[0])?.id ?? null
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
  const ti = tables.findIndex((tbl) => tbl.table === cell.table)
  const tbl = tables[ti]
  const ri = tbl.rows.findIndex((r) => r.index === cell.index)
  const ci = tbl.columns.indexOf(cell.column)
  const at = (r: number, c: number) => tbl.rows[r]?.cells.get(tbl.columns[c])?.id ?? null
  if (key === 'ArrowLeft') {
    return ci > 0 ? at(ri, ci - 1) : null
  }
  if (key === 'ArrowRight') {
    return ci < tbl.columns.length - 1 ? at(ri, ci + 1) : null
  }
  if (key === 'ArrowUp') {
    if (ri > 0) {
      return at(ri - 1, ci)
    }
    return ti > 0 ? firstCell(tables[ti - 1]) : (plain[plain.length - 1]?.id ?? null)
  }
  if (ri < tbl.rows.length - 1) {
    return at(ri + 1, ci)
  }
  return firstCell(tables[ti + 1])
}
