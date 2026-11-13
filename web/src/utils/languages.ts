import { useState } from 'react'

/**
 * 模板字段名的语言（ADR 0048）。代码是 BCP 47 的简写；名字用那种语言自己的写法，
 * 中文用户也能认出"English""日本語"。
 */
export const LANGUAGE_NAMES: Record<string, string> = {
  zh: '中文',
  'zh-Hant': '繁體中文',
  en: 'English',
  ja: '日本語',
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

/** 下拉框的选项：按给的顺序，第一个一般是中文。 */
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
    // 浏览器不让存（隐私模式等）就只在这一页里记着
  }
}

/**
 * 记住选的语言（只在这个浏览器里，按 key 分开记，例如每个模板一份）。
 * 选的语言这个模板没有时，用第一个（中文）。
 */
export function useLanguage(key: string, available: string[]): [string, (code: string) => void] {
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const stored = chosen[key] ?? read(`jacara.language.${key}`) ?? 'zh'
  const language = available.includes(stored) ? stored : (available[0] ?? 'zh')
  const choose = (code: string) => {
    write(`jacara.language.${key}`, code)
    setChosen((old) => ({ ...old, [key]: code }))
  }
  return [language, choose]
}
