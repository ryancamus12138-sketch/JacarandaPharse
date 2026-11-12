/** 模板编辑器共用的小工具（字段 ID 规则、类型选项、新字段）。 */

import type { FieldType, TemplateField } from '../../api/types'

export const TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: 'string', label: '文字' },
  { value: 'number', label: '数字（金额）' },
  { value: 'date', label: '日期' },
]

/** 地区的名字：模板和地区规则都用（ADR 0035）。 */
export const REGION_TEXT: Record<string, string> = { '': '通用', cn: '中国', au: '澳洲' }

/** 模板的类型（上传时按它分组）。可以自己写别的。 */
export const CATEGORIES = ['发票', '交通', '收据', '住宿', '其他']

/** 字段 ID 的规则（和后端一样）：小写英文字母开头，只有小写字母、数字、下划线。 */
export const FIELD_ID = /^[a-z][a-z0-9_]*$/

export function blankField(existing: TemplateField[]): TemplateField {
  let n = existing.length + 1
  while (existing.some((f) => f.name === `field_${n}`)) n++
  return {
    name: `field_${n}`,
    label: '',
    type: 'string',
    required: false,
    description: '',
    aliases: [],
    export: true,
    sensitive: false,
    options: [],
    extract: true,
  }
}

/** 保存前检查：中文名不能空、字段 ID 合规且不重复。返回第一个问题，没问题返回 null。 */
export function fieldsProblem(fields: TemplateField[], where: string): string | null {
  const seen = new Set<string>()
  for (const f of fields) {
    if (!f.label.trim()) return `${where}有字段没写中文名`
    if (!FIELD_ID.test(f.name)) return `${where}的字段 ID「${f.name}」只能用小写英文字母、数字和下划线`
    if (seen.has(f.name)) return `${where}的字段 ID「${f.name}」重复了`
    seen.add(f.name)
  }
  return null
}
