/** 重新处理的选项（单份和批量共用）。 */

export const SAME = '__same__'

export const SPLITS = [
  { value: 'auto', label: '自动判断' },
  { value: 'one', label: '整个文件是一张单据' },
  { value: 'pages', label: '每页是一张单据' },
  { value: 'ranges', label: '自己写页码' },
]

export type Vision = 'default' | 'always' | 'off'

export const VISION_CHOICES: { value: Vision; label: string }[] = [
  { value: 'default', label: '系统决定（推荐）' },
  { value: 'always', label: '每页都对照原图' },
  { value: 'off', label: '只看文字（最快）' },
]

export type PdfRead = 'default' | 'image' | 'text'

export const PDF_READ_CHOICES: { value: PdfRead; label: string }[] = [
  { value: 'default', label: '系统决定（推荐）' },
  { value: 'image', label: '画成图识别' },
  { value: 'text', label: '只读 PDF 自带的文字（最快）' },
]
