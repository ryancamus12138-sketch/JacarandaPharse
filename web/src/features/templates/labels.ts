/** Template tags: domains, countries and regions (or generic), custom tags. */

import { useQuery } from '@tanstack/react-query'
import { listDomains, listTemplates } from '../../api/templates'
import type { Template } from '../../api/types'
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

/** Colors of known domains, by their stored form (the names shown follow the interface language). */
// i18n-ignore-next-line: stored forms of known domains, not text shown as such
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
    { label: t('通用'), title: 'general', options: [{ value: GENERAL, label: t('通用（适用于所有国家）') }] },
    { label: t('常用'), title: 'common', options: COMMON.map(option) },
    { label: t('全部国家和地区'), title: 'all', options: rest.map(option) },
  ]
}


/** A template's domains with their names in the interface language (stored form → name). */
export function domainPairs(tpl: Template): { value: string; label: string }[] {
  const domains = tpl.domains?.length ? tpl.domains : tpl.category ? [tpl.category] : []
  // The names belong to the domains the server sent; a template whose domains are being edited may differ.
  const named = tpl.domain_names?.length === domains.length
  return domains.map((value, i) => ({ value, label: named ? (tpl.domain_names?.[i] ?? value) : value }))
}

/**
 * Tag suggestions: known domains (named in the interface language) plus domains and custom tags already used
 * (collected from my templates).
 */
export function useLabelVocabulary(): { domains: { value: string; label: string }[]; tags: string[] } {
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const known = useQuery({ queryKey: ['template-domains'], queryFn: listDomains, staleTime: Infinity })
  const mine = (templates.data ?? []).filter((tpl) => tpl.kind === 'mine')
  const domains = new Map((known.data ?? []).map((d) => [d.value, d.label]))
  for (const pair of mine.flatMap(domainPairs)) {
    if (!domains.has(pair.value)) domains.set(pair.value, pair.label)
  }
  const tags = [...new Set(mine.flatMap((tpl) => tpl.tags ?? []))].sort((a, b) => a.localeCompare(b, uiLanguage()))
  return { domains: [...domains].map(([value, label]) => ({ value, label })), tags }
}
