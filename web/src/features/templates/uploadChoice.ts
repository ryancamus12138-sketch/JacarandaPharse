/** Template selection at upload: options and the remembered choice. */

import type { Template } from '../../api/types'
import { countryName } from './labels'
import { t } from '../../i18n'

/** Value of "no template, extract all key-value pairs" (same as the backend). */
export const DISCOVER = 'discover'

/** Value of "select the template automatically" (same as the backend). */
export const AUTO = 'auto'

const KEY = 'jacara.upload.template'

/**
 * Template chosen for the last upload (remembered in this browser). undefined means the backend uses this
 * user's last template.
 */
export function loadUploadTemplate(): string | undefined {
  try {
    return localStorage.getItem(KEY) ?? undefined
  } catch {
    return undefined
  }
}

export function saveUploadTemplate(value: string) {
  try {
    localStorage.setItem(KEY, value)
  } catch {
    // Storage unavailable: ignore.
  }
}

/**
 * My templates grouped by their first domain; names are followed by countries (omitted for generic) and are
 * searchable by name, domain, country and tag.
 */
export function templateOptions(templates: Template[]) {
  const mine = templates.filter((tpl) => tpl.kind === 'mine' && !tpl.archived && tpl.id)
  const groups = new Map<string, Template[]>()
  for (const tpl of mine) {
    const key = tpl.domains?.[0] || tpl.category || t('其他')
    groups.set(key, [...(groups.get(key) ?? []), tpl])
  }
  const label = (tpl: Template) => {
    const where = (tpl.countries ?? []).map(countryName).join(t('、'))
    return t('{{title}}{{value}}（第 {{version}} 版）', { title: tpl.title, value: where ? ` · ${where}` : '', version: tpl.version })
  }
  const auto = mine.length
    ? [
        {
          label: t('让系统选'),
          title: 'auto',
          options: [
            {
              value: AUTO,
              label: t('自动判断（从我的模板里选最合适的）'),
              search: t('自动判断 auto 让系统选'),
            },
          ],
        },
      ]
    : []
  return [
    ...auto,
    ...[...groups].map(([category, items]) => ({
      label: category,
      title: category,
      options: items.map((tpl) => ({
        value: tpl.id as string,
        label: label(tpl),
        // Search also matches domains, countries and custom tags.
        search: [label(tpl), ...(tpl.domains ?? []), ...(tpl.tags ?? [])].join(' '),
      })),
    })),
    {
      label: t('没有合适的模板'),
      title: 'discover',
      options: [
        { value: DISCOVER, label: t('抽取所有键值对（之后可以存成模板）'), search: t('抽取所有键值对 discover') },
      ],
    },
  ]
}
