import type { TemplateField, TemplateIn, TemplateTable } from '../../api/types'
import { uiLanguage } from '../../i18n'

/**
 * Texts of a template in several languages. label, title and description are written in the template's primary
 * language; names, titles and descriptions hold the other languages. Mirrors with_language in the backend
 * (jacara/templates/__init__.py).
 */
export type Texts = Record<string, string> | undefined

export const DEFAULT_LANGUAGE = 'zh'

/** Text in a language: the primary text for the primary language, otherwise that language's text if written. */
export function textIn(primary: string, others: Texts, primaryLanguage: string, language: string): string {
  return language === primaryLanguage ? primary : (others?.[language] || primary)
}

/** Whether the item has its own text in the language (the primary text counts for the primary language). */
export function hasText(primary: string, others: Texts, primaryLanguage: string, language: string): boolean {
  return language === primaryLanguage ? Boolean(primary) : Boolean(others?.[language])
}

/**
 * Move others[next] into the primary text and the primary text into others[previous]. Nothing changes when
 * there is no text in next, so nothing is lost and switching back restores the original.
 */
function swap(primary: string, others: Texts, previous: string, next: string): [string, Record<string, string>] {
  const rest = { ...(others ?? {}) }
  const text = rest[next]
  delete rest[next]
  if (!text) return [primary, others ?? {}]
  if (primary) rest[previous] = primary
  return [text, rest]
}

function swapField<T extends TemplateField | TemplateTable>(item: T, previous: string, next: string): T {
  const [label, names] = swap(item.label, item.names, previous, next)
  const [description, descriptions] = swap(item.description, item.descriptions, previous, next)
  return { ...item, label, names, description, descriptions }
}

/** Number of names and descriptions (written in the current primary language) without a version in next. */
export function untranslated(draft: TemplateIn, next: string): number {
  const items: { primary: string; others: Texts }[] = [
    { primary: draft.title, others: draft.titles },
    { primary: draft.description, others: draft.descriptions },
  ]
  const add = (f: TemplateField | TemplateTable) => {
    items.push({ primary: f.label, others: f.names }, { primary: f.description, others: f.descriptions })
  }
  draft.fields.forEach(add)
  draft.tables.forEach((tbl) => {
    add(tbl)
    tbl.columns.forEach(add)
  })
  return items.filter((i) => i.primary && !i.others?.[next]).length
}

/** The draft with next as its primary language (domains and tags are the user's own labels and stay as they are). */
export function withLanguage(draft: TemplateIn, next: string): TemplateIn {
  const previous = draft.language ?? DEFAULT_LANGUAGE
  if (next === previous) return draft
  const [title, titles] = swap(draft.title, draft.titles, previous, next)
  const [description, descriptions] = swap(draft.description, draft.descriptions, previous, next)
  return {
    ...draft,
    language: next,
    title,
    titles,
    description,
    descriptions,
    fields: draft.fields.map((f) => swapField(f, previous, next)),
    tables: draft.tables.map((tbl) => ({
      ...swapField(tbl, previous, next),
      columns: tbl.columns.map((c) => swapField(c, previous, next)),
    })),
  }
}

/** Primary language of a new template: the interface language. */
export function newTemplateLanguage(): string {
  return uiLanguage() === 'en' ? 'en' : DEFAULT_LANGUAGE
}
