/**
 * Interface language (zh-CN or en).
 *
 * Keys are the Chinese source text (the gettext msgid model): Chinese needs no resource file because a
 * missing key renders the key itself, and en.json maps each Chinese source string to English. Separators
 * are disabled because source text contains "." and ":".
 *
 * The interface language is independent of the template field-name language (utils/languages.ts).
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './en.json'

export type UiLanguage = 'zh-CN' | 'en'

export const UI_LANGUAGES: readonly UiLanguage[] = ['zh-CN', 'en']

const STORAGE_KEY = 'jacara.uiLanguage'

function stored(): UiLanguage | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'zh-CN' || value === 'en' ? value : null
  } catch {
    return null
  }
}

/** First visit: Chinese for browsers set to Chinese, English otherwise. */
function detect(): UiLanguage {
  return stored() ?? (navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en')
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: detect(),
  fallbackLng: false,
  supportedLngs: [...UI_LANGUAGES],
  keySeparator: false,
  nsSeparator: false,
  returnEmptyString: false,
  interpolation: { escapeValue: false },
})
document.documentElement.lang = i18n.language

/** Current interface language. */
export function uiLanguage(): UiLanguage {
  return i18n.language === 'en' ? 'en' : 'zh-CN'
}

/** Switch the interface language and remember the choice in this browser. */
export function setUiLanguage(language: UiLanguage): void {
  try {
    localStorage.setItem(STORAGE_KEY, language)
  } catch {
    // Storage unavailable: applies to this page only.
  }
  document.documentElement.lang = language
  void i18n.changeLanguage(language)
}

/**
 * Marks a string for translation without translating it (like gettext_noop), for tables defined at module
 * level. Translate at render time with t(value).
 */
export function msg<T extends string>(text: T): T {
  return text
}

/** Translation outside React components (event handlers, utilities). Components use useTranslation(). */
export const t = i18n.t.bind(i18n)

export default i18n
