/** Filtering and sorting on the templates page. */

import type { Template } from '../../api/types'
import { t } from '../../i18n'

/** Filters of the templates page. */
export interface Filters {
  q: string
  domains: string[]
  /** Country code; "" means generic only; undefined means unrestricted. */
  country?: string
  tags: string[]
  source?: string
  sort: 'updated' | 'used_recent' | 'used_most' | 'name'
}

export const NO_FILTERS: Filters = { q: '', domains: [], tags: [], sort: 'updated' }

/** Source, derived from the source field (starter:xxx, copy:xxx, sample:xxx, excel, import, blank). */
export const SOURCE_TEXT: Record<string, string> = {
  get starter() { return t('从起步模板库') },
  get copy() { return t('复制的') },
  get excel() { return t('从 Excel 建') },
  get sample() { return t('用样例建') },
  get import() { return t('导入的') },
  get blank() { return t('空白新建') },
}

export function sourceKind(source: string | undefined): string {
  return (source ?? '').split(':')[0] || 'blank'
}

export function domainsOf(tpl: Template): string[] {
  return tpl.domains?.length ? tpl.domains : tpl.category ? [tpl.category] : []
}

/** Filter and sort by the given criteria. */
export function applyFilters(list: Template[], f: Filters): Template[] {
  const q = f.q.trim().toLowerCase()
  const found = list.filter((tpl) => {
    if (q && !`${tpl.title} ${tpl.description}`.toLowerCase().includes(q)) return false
    if (f.domains.length && !f.domains.some((d) => domainsOf(tpl).includes(d))) return false
    const countries = tpl.countries ?? []
    if (f.country === '' && countries.length > 0) return false
    if (f.country && !countries.includes(f.country)) return false
    if (f.tags.length && !f.tags.some((x) => (tpl.tags ?? []).includes(x))) return false
    if (f.source && sourceKind(tpl.source) !== f.source) return false
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
