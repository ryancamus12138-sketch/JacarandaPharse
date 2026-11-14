import { useState } from 'react'

/**
 * Languages of template field names. Codes are short BCP 47 tags; names are written in the language itself
 * (endonyms), so users recognize "English" and "日本語" regardless of UI language.
 */
export const LANGUAGE_NAMES: Record<string, string> = {
  zh: '中文', // i18n-ignore: endonym
  'zh-Hant': '繁體中文', // i18n-ignore: endonym
  en: 'English',
  ja: '日本語', // i18n-ignore: endonym
  ko: '한국어',
  ms: 'Bahasa Melayu',
  vi: 'Tiếng Việt',
  th: 'ไทย',
  id: 'Bahasa Indonesia',
  fil: 'Filipino',
  hi: 'हिन्दी',
  de: 'Deutsch',
  fr: 'Français',
  it: 'Italiano',
  es: 'Español',
  nl: 'Nederlands',
  pl: 'Polski',
  ar: 'العربية',
  mi: 'Te Reo Māori',
}

export function languageName(code: string): string {
  return LANGUAGE_NAMES[code] ?? code
}

/** Select options in the given order; the first is usually Chinese. */
export function languageOptions(codes: string[]): { value: string; label: string }[] {
  return codes.map((code) => ({ value: code, label: languageName(code) }))
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // When the browser forbids storage (private mode and similar), remember the choice for this page only.
  }
}

/**
 * Remember the selected language (in this browser only, separately per key, for example per template). When
 * nothing was selected or the template lacks the selected language, the first one (the template's primary
 * language) is used.
 */
export function useLanguage(key: string, available: string[]): [string, (code: string) => void] {
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const stored = chosen[key] ?? read(`jacara.language.${key}`)
  const language = stored && available.includes(stored) ? stored : (available[0] ?? 'zh')
  const choose = (code: string) => {
    write(`jacara.language.${key}`, code)
    setChosen((old) => ({ ...old, [key]: code }))
  }
  return [language, choose]
}
