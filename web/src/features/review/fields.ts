/** 字段的中文名和状态说明。后端只给英文字段名，显示给人看的名字放在这里。 */

import type { FieldValue } from '../../api/types'

const LABELS: Record<string, string> = {
  merchant: '商家',
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

/** 字段的中文名；没登记的就显示英文原名。 */
export function fieldLabel(name: string): string {
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
