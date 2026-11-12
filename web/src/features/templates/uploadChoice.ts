/** 上传时选模板：选项、记住上次选的。 */

import type { Template } from '../../api/types'
import { countryName } from './labels'

/** "不用模板、抽出所有键值对"在选择里的值（和后端一样）。 */
export const DISCOVER = 'discover'

const KEY = 'jacara.upload.template'

/** 上次上传选的模板（记在这个浏览器里）。没有就是 undefined：后端用这个人上一次用的。 */
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
    // 存不了就算了
  }
}

/** 我的模板按第一个功能领域分组；名字后面带上国家（通用的不写），可以按名字、领域、国家、标签搜。 */
export function templateOptions(templates: Template[]) {
  const mine = templates.filter((t) => t.kind === 'mine' && !t.archived && t.id)
  const groups = new Map<string, Template[]>()
  for (const t of mine) {
    const key = t.domains?.[0] || t.category || '其他'
    groups.set(key, [...(groups.get(key) ?? []), t])
  }
  const label = (t: Template) => {
    const where = (t.countries ?? []).map(countryName).join('、')
    return `${t.title}${where ? ` · ${where}` : ''}（第 ${t.version} 版）`
  }
  return [
    ...[...groups].map(([category, items]) => ({
      label: category,
      title: category,
      options: items.map((t) => ({
        value: t.id as string,
        label: label(t),
        // 搜索时也能按领域、国家、自定义标签找
        search: [label(t), ...(t.domains ?? []), ...(t.tags ?? [])].join(' '),
      })),
    })),
    {
      label: '没有合适的模板',
      title: 'discover',
      options: [
        { value: DISCOVER, label: '抽取所有键值对（之后可以存成模板）', search: '抽取所有键值对 discover' },
      ],
    },
  ]
}
