/** Excel 建模板时 sheet 的角色、列的来源的中文名（ADR 0031）。 */

import type { ColumnSource, SheetRole } from '../../api/types'

export const ROLE_TEXT: Record<SheetRole, { label: string; help: string; color: string }> = {
  document: { label: '单据表', help: '一份单据一行', color: 'purple' },
  items: { label: '明细表', help: '一份单据多行（商品、检测项目……）', color: 'blue' },
  entity: { label: '对象表', help: '同一个人、同一家公司一行，几份单据按编号合并', color: 'magenta' },
  reference: { label: '参照表', help: '固定名单，导入一次，用来对照、补编号', color: 'default' },
  options: { label: '选项', help: '下拉框用的可选值，不建字段', color: 'default' },
  ignore: { label: '不用', help: '说明、备注，或者用不上的', color: 'default' },
}

export const SOURCE_TEXT: Record<ColumnSource, string> = {
  extract: '从单据抽',
  manual: '人工填',
  doc: '单据上的字段（每行重复）',
  auto: '系统自动',
  lookup: '从参照表对照',
  none: '不填',
}

/** 这一列会不会变成模板里的字段（要有字段 ID）。 */
export function makesField(source: ColumnSource): boolean {
  return source === 'extract' || source === 'manual' || source === 'doc'
}
