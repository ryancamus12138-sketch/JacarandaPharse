/** 模板页的筛选和排序（ADR 0037）。 */

import type { Template } from '../../api/types'

/** 模板页的筛选条件（ADR 0037）。 */
export interface Filters {
  q: string
  domains: string[]
  /** 国家代码；"" 表示只看通用的；undefined 不限 */
  country?: string
  tags: string[]
  source?: string
  sort: 'updated' | 'used_recent' | 'used_most' | 'name'
}

export const NO_FILTERS: Filters = { q: '', domains: [], tags: [], sort: 'updated' }

/** 来源：从 source 字段认（starter:xxx、copy:xxx、sample:xxx、excel、import、blank）。 */
export const SOURCE_TEXT: Record<string, string> = {
  starter: '从起步模板库',
  copy: '复制的',
  excel: '从 Excel 建',
  sample: '用样例建',
  import: '导入的',
  blank: '空白新建',
}

export function sourceKind(source: string | undefined): string {
  return (source ?? '').split(':')[0] || 'blank'
}

export function domainsOf(t: Template): string[] {
  return t.domains?.length ? t.domains : t.category ? [t.category] : []
}

/** 按条件筛选、排序。 */
export function applyFilters(list: Template[], f: Filters): Template[] {
  const q = f.q.trim().toLowerCase()
  const found = list.filter((t) => {
    if (q && !`${t.title} ${t.description}`.toLowerCase().includes(q)) return false
    if (f.domains.length && !f.domains.some((d) => domainsOf(t).includes(d))) return false
    const countries = t.countries ?? []
    if (f.country === '' && countries.length > 0) return false
    if (f.country && !countries.includes(f.country)) return false
    if (f.tags.length && !f.tags.some((x) => (t.tags ?? []).includes(x))) return false
    if (f.source && sourceKind(t.source) !== f.source) return false
    return true
  })
  const time = (s: string | null | undefined) => (s ? new Date(s).getTime() : 0)
  return [...found].sort((a, b) => {
    if (f.sort === 'name') return a.title.localeCompare(b.title, 'zh-CN')
    if (f.sort === 'used_most') return (b.used ?? 0) - (a.used ?? 0)
    if (f.sort === 'used_recent') return time(b.last_used_at) - time(a.last_used_at)
    return time(b.updated_at) - time(a.updated_at)
  })
}
