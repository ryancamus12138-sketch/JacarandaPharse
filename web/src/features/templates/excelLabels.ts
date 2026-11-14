/** Display names of sheet roles and column sources when creating templates from Excel. */

import type { ColumnSource, SheetRole } from '../../api/types'
import { t } from '../../i18n'

export const ROLE_TEXT: Record<SheetRole, { label: string; help: string; color: string }> = {
  document: { get label() { return t('单据表') }, get help() { return t('一份单据一行') }, color: 'purple' },
  items: { get label() { return t('明细表') }, get help() { return t('一份单据多行（商品、检测项目……）') }, color: 'blue' },
  entity: { get label() { return t('对象表') }, get help() { return t('同一个人、同一家公司一行，几份单据按编号合并') }, color: 'magenta' },
  reference: { get label() { return t('参照表') }, get help() { return t('固定名单，导入一次，用来对照、补编号') }, color: 'default' },
  options: { get label() { return t('选项') }, get help() { return t('下拉框用的可选值，不建字段') }, color: 'default' },
  ignore: { get label() { return t('不用') }, get help() { return t('说明、备注，或者用不上的') }, color: 'default' },
}

export const SOURCE_TEXT: Record<ColumnSource, string> = {
  get extract() { return t('从单据抽') },
  get manual() { return t('人工填') },
  get doc() { return t('单据上的字段（每行重复）') },
  get auto() { return t('系统自动') },
  get lookup() { return t('从参照表对照') },
  get none() { return t('不填') },
}

/** Whether this column becomes a template field (requires a field ID). */
export function makesField(source: ColumnSource): boolean {
  return source === 'extract' || source === 'manual' || source === 'doc'
}
