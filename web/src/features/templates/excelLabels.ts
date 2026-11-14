/** Display names of sheet roles and column sources when creating templates from Excel. */

import type { ColumnSource, SheetRole } from '../../api/types'
import { t } from '../../i18n'

export const ROLE_TEXT: Record<SheetRole, { label: string; help: string; color: string }> = {
  document: { get label() { return t('单据表') }, get help() { return t('每份单据一行') }, color: 'purple' },
  items: { get label() { return t('明细表') }, get help() { return t('每份单据多行（如商品、检测项目）') }, color: 'blue' },
  entity: { get label() { return t('对象表') }, get help() { return t('同一个人或公司一行，多份单据按编号合并') }, color: 'magenta' },
  reference: { get label() { return t('参照表') }, get help() { return t('固定清单，一次导入，用于对照和补全编号') }, color: 'default' },
  options: { get label() { return t('选项') }, get help() { return t('下拉框的可选值，不创建字段') }, color: 'default' },
  ignore: { get label() { return t('忽略') }, get help() { return t('说明、备注或无需使用的内容') }, color: 'default' },
}

export const SOURCE_TEXT: Record<ColumnSource, string> = {
  get extract() { return t('从单据抽取') },
  get manual() { return t('人工填写') },
  get doc() { return t('单据级字段（每行重复）') },
  get auto() { return t('系统自动填充') },
  get lookup() { return t('从参照表对照') },
  get none() { return t('不填') },
}

/** Whether this column becomes a template field (requires a field ID). */
export function makesField(source: ColumnSource): boolean {
  return source === 'extract' || source === 'manual' || source === 'doc'
}
