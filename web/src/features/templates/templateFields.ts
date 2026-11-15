/** Shared helpers of the template editor (field ID rules, type options, new fields). */

import type { FieldType, RangeExport, TemplateField } from '../../api/types'
import { t } from '../../i18n'

export const TYPE_OPTIONS: { value: FieldType; label: string }[] = [
  { value: 'string', get label() { return t('文字') } },
  { value: 'number', get label() { return t('数字（金额）') } },
  { value: 'date', get label() { return t('日期') } },
  { value: 'date_range', get label() { return t('日期范围') } },
]

/** Date range export formats. */
export const RANGE_EXPORTS: { value: RangeExport; label: string }[] = [
  { value: 'normalized', get label() { return t('统一格式') } },
  { value: 'original', get label() { return t('原文') } },
  { value: 'columns', get label() { return t('拆分为两列') } },
]

/** Template types (used to group templates at upload). Others can be typed. */
export const CATEGORIES = ['发票', '交通', '收据', '住宿', '其他'] // i18n-ignore: template content

/**
 * Field ID rule (same as the backend): starts with a lowercase letter; only lowercase letters, digits and
 * underscores.
 */
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

/**
 * Checks before saving: display names must not be empty; field IDs must be valid and unique. Returns the
 * first problem, or null.
 */
export function fieldsProblem(fields: TemplateField[], where: string): string | null {
  const seen = new Set<string>()
  for (const f of fields) {
    if (!f.label.trim()) return t('{{where}}存在未填写名称的字段', { where })
    if (!FIELD_ID.test(f.name)) return t('{{where}}的字段 ID「{{name}}」只能包含小写英文字母、数字和下划线', { where, name: f.name })
    if (seen.has(f.name)) return t('{{where}}的字段 ID「{{name}}」重复', { where, name: f.name })
    seen.add(f.name)
  }
  return null
}
