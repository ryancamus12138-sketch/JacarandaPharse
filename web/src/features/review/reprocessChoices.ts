import { t } from '../../i18n'
/** Reprocessing options (shared by single and bulk reprocessing). */

export const SAME = '__same__'

export const SPLITS = [
  { value: 'auto', get label() { return t('自动判断') } },
  { value: 'one', get label() { return t('整个文件是一张单据') } },
  { value: 'pages', get label() { return t('每页是一张单据') } },
  { value: 'ranges', get label() { return t('自己写页码') } },
]

export type Vision = 'default' | 'always' | 'off'

export const VISION_CHOICES: { value: Vision; label: string }[] = [
  { value: 'default', get label() { return t('系统决定（推荐）') } },
  { value: 'always', get label() { return t('每页都对照原图') } },
  { value: 'off', get label() { return t('只看文字（最快）') } },
]

export type PdfRead = 'default' | 'image' | 'text'

export const PDF_READ_CHOICES: { value: PdfRead; label: string }[] = [
  { value: 'default', get label() { return t('系统决定（推荐）') } },
  { value: 'image', get label() { return t('画成图识别') } },
  { value: 'text', get label() { return t('只读 PDF 自带的文字（最快）') } },
]
