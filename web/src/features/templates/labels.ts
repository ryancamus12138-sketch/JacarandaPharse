/** Template tags: domains, countries and regions (or generic), custom tags. */

import { useQuery } from '@tanstack/react-query'
import { listTemplates } from '../../api/templates'
import { t, uiLanguage } from '../../i18n'

/**
 * Two-letter codes of all ISO 3166-1 countries and regions. Names are localized by the browser (countryName).
 */
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

/** Common ones first. */
const COMMON = ['CN', 'AU', 'US', 'GB', 'HK', 'SG', 'JP', 'NZ', 'CA', 'DE', 'FR', 'MY']

const displayNames = new Map<string, Intl.DisplayNames | null>()

/** Region names in the interface language (cached per language). */
function regionNames(): Intl.DisplayNames | null {
  const language = uiLanguage()
  if (!displayNames.has(language)) {
    try {
      displayNames.set(language, new Intl.DisplayNames([language], { type: 'region' }))
    } catch {
      displayNames.set(language, null)
    }
  }
  return displayNames.get(language) ?? null
}

/** CN → China. Shows the code when the browser does not know it. */
export function countryName(code: string): string {
  try {
    return regionNames()?.of(code) ?? code
  } catch {
    return code
  }
}

/** Common domains (others can be typed). */
// i18n-ignore-next-line: template content (domain values stored in templates)
export const DOMAIN_SUGGESTIONS = ['发票', '收据', '交通', '住宿', '餐饮', '购物', '医疗', '检验报告', '质检', '物流', '外贸单证', '采购', '办公', '其他']

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

/** Country options: "generic" first, then common countries, then all (sorted by localized name). */
export function countryOptions() {
  const rest = COUNTRY_CODES.filter((c) => !COMMON.includes(c)).sort((a, b) =>
    countryName(a).localeCompare(countryName(b), uiLanguage()),
  )
  const option = (c: string) => ({ value: c, label: t('{{countryName}}（{{c}}）', { countryName: countryName(c), c }) })
  return [
    { label: t('不分国家'), title: 'general', options: [{ value: GENERAL, label: t('通用（不分国家）') }] },
    { label: t('常用'), title: 'common', options: COMMON.map(option) },
    { label: t('全部国家和地区'), title: 'all', options: rest.map(option) },
  ]
}


/**
 * Tag suggestions: common domains plus domains and custom tags already used (collected from my templates).
 */
export function useLabelVocabulary(): { domains: string[]; tags: string[] } {
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const mine = (templates.data ?? []).filter((tpl) => tpl.kind === 'mine')
  const domains = [...new Set([...DOMAIN_SUGGESTIONS, ...mine.flatMap((tpl) => tpl.domains ?? [])])]
  const tags = [...new Set(mine.flatMap((tpl) => tpl.tags ?? []))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  return { domains, tags }
}
