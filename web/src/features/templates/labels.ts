/** 模板的标签（ADR 0037）：功能领域、国家和地区（或通用）、自定义标签。 */

import { useQuery } from '@tanstack/react-query'
import { listTemplates } from '../../api/templates'

/** ISO 3166-1 全部国家和地区的两位代码。中文名由浏览器翻译（countryName）。 */
export const COUNTRY_CODES = (
  'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS ' +
  'BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE ' +
  'EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM ' +
  'HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC ' +
  'LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA ' +
  'NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW ' +
  'SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO ' +
  'TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'
).split(' ')

/** 常用的放在最前面。 */
const COMMON = ['CN', 'AU', 'US', 'GB', 'HK', 'SG', 'JP', 'NZ', 'CA', 'DE', 'FR', 'MY']

let names: Intl.DisplayNames | null = null
try {
  names = new Intl.DisplayNames(['zh-CN'], { type: 'region' })
} catch {
  names = null
}

/** CN → 中国。浏览器不认识就显示代码。 */
export function countryName(code: string): string {
  try {
    return names?.of(code) ?? code
  } catch {
    return code
  }
}

/** 常用的功能领域（也可以自己写别的）。 */
export const DOMAIN_SUGGESTIONS = ['发票', '收据', '交通', '住宿', '餐饮', '购物', '医疗', '物流', '办公', '其他']

export const DOMAIN_COLORS: Record<string, string> = {
  发票: 'purple',
  交通: 'blue',
  收据: 'green',
  住宿: 'orange',
  餐饮: 'volcano',
  购物: 'cyan',
  医疗: 'red',
}

export const GENERAL = '__general__'

/** 选国家的下拉选项：最上面"通用"，再是常用国家，再是全部（按中文名排序）。 */
export function countryOptions() {
  const rest = COUNTRY_CODES.filter((c) => !COMMON.includes(c)).sort((a, b) =>
    countryName(a).localeCompare(countryName(b), 'zh-CN'),
  )
  const option = (c: string) => ({ value: c, label: `${countryName(c)}（${c}）` })
  return [
    { label: '不分国家', title: 'general', options: [{ value: GENERAL, label: '通用（不分国家）' }] },
    { label: '常用', title: 'common', options: COMMON.map(option) },
    { label: '全部国家和地区', title: 'all', options: rest.map(option) },
  ]
}


/** 选标签时的候选：常用的功能领域 + 已经用过的领域、自定义标签（从我的模板里收集）。 */
export function useLabelVocabulary(): { domains: string[]; tags: string[] } {
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const mine = (templates.data ?? []).filter((t) => t.kind === 'mine')
  const domains = [...new Set([...DOMAIN_SUGGESTIONS, ...mine.flatMap((t) => t.domains ?? [])])]
  const tags = [...new Set(mine.flatMap((t) => t.tags ?? []))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  return { domains, tags }
}
