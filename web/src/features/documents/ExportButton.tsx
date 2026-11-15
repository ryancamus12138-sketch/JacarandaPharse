import { CheckOutlined, DownloadOutlined, DownOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, type ButtonProps, Dropdown, type MenuProps, Space } from 'antd'
import { ApiError } from '../../api/client'
import { type ExportMode, exportDocuments } from '../../api/documents'
import { saveFile } from '../../utils/download'
import { languageName, useLanguage } from '../../utils/languages'
import { useTranslation } from 'react-i18next'
import { uiLanguage } from '../../i18n'

const MAX_EXPORT = 100 // Same as the backend: at most 100 documents per export.

interface Props {
  ids: string[]
  type?: ButtonProps['type']
  /**
   * Languages available for column headers; the first is the default (the template's primary language).
   * Defaults to the interface language, then the other of Chinese and English.
   */
  languages?: string[]
  /**
   * Language controlled by the parent (the review page shares it with field names, so selecting English also
   * exports English). Kept locally when omitted.
   */
  language?: string
  onLanguage?: (code: string) => void
}

function defaultLanguages(): string[] {
  return uiLanguage() === 'en' ? ['en', 'zh'] : ['zh', 'en']
}

/**
 * Export button.
 * - One document: download its workbook directly;
 * - Several documents: the main button downloads a zip (one workbook per document); the arrow next to it
 *   offers "summary in one workbook".
 */
export function ExportButton({ ids, type, languages = defaultLanguages(), language: shown, onLanguage }: Props) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [own, setOwn] = useLanguage('export', languages)
  const language = shown ?? own
  const setLanguage = onLanguage ?? setOwn
  const download = useMutation({
    mutationFn: (mode: ExportMode) => exportDocuments(ids, mode, language),
    onSuccess: (file) => {
      void saveFile(file)
      void message.success(t('已导出 {{filename}}（可在导出记录中重新下载）', { filename: file.filename }))
      void queryClient.invalidateQueries({ queryKey: ['exports'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const tooMany = ids.length > MAX_EXPORT
  const disabled = ids.length === 0 || tooMany
  const title = tooMany ? t('单次最多导出 {{MAX_EXPORT}} 份', { MAX_EXPORT }) : undefined

  const csv = { key: 'csv', label: t('导出为 CSV') }
  const json = { key: 'json', label: t('导出为 JSON（供其他程序使用）') }
  // Column header language: remembered for later exports; selecting it does not export.
  const languageMenu: MenuProps['items'] =
    languages.length > 1
      ? [
          { type: 'divider' },
          {
            type: 'group',
            label: t('列名语言'),
            children: languages.map((code) => ({
              key: `lang:${code}`,
              label: languageName(code),
              icon: code === language ? <CheckOutlined /> : <span style={{ display: 'inline-block', width: 14 }} />,
            })),
          },
        ]
      : []
  const onMenu = (key: string, other: () => void) => {
    if (key.startsWith('lang:')) {
      setLanguage(key.slice(5))
      void message.info(t('导出列名已改为{{languageName}}', { languageName: languageName(key.slice(5)) }))
      return
    }
    other()
  }
  const suffix = language !== (languages[0] ?? 'zh') ? t('（{{languageName}}）', { languageName: languageName(language) }) : ''

  if (ids.length <= 1) {
    return (
      <Space.Compact>
        <Button
          type={type}
          icon={<DownloadOutlined />}
          disabled={disabled}
          loading={download.isPending && download.variables === 'separate'}
          onClick={() => download.mutate('separate')}
        >
          {t('导出 Excel{{suffix}}', { suffix })}
        </Button>
        <Dropdown
          disabled={disabled}
          placement="bottomRight"
          menu={{
            items: [csv, json, ...languageMenu],
            onClick: ({ key }) => onMenu(key, () => download.mutate(key === 'json' ? 'json' : 'csv')),
          }}
        >
          <Button
            type={type}
            icon={<DownOutlined />}
            aria-label={t('更多导出格式')}
            loading={download.isPending && download.variables !== 'separate'}
          />
        </Dropdown>
      </Space.Compact>
    )
  }

  return (
    // Space.Compact: the two buttons are joined and look like one.
    <Space.Compact>
      <Button
        type={type}
        icon={<DownloadOutlined />}
        disabled={disabled}
        title={title}
        loading={download.isPending && download.variables === 'separate'}
        onClick={() => download.mutate('separate')}
      >
        {t('导出 {{length}} 份 Excel{{suffix}}', { length: ids.length, suffix })}
      </Button>
      <Dropdown
        disabled={disabled}
        placement="bottomRight"
        menu={{
          items: [{ key: 'summary', label: t('汇总至一个 Excel') }, csv, json, ...languageMenu],
          onClick: ({ key }) =>
            onMenu(key, () => download.mutate(key === 'csv' || key === 'json' ? key : 'summary')),
        }}
      >
        <Button
          type={type}
          icon={<DownOutlined />}
          aria-label={t('更多导出格式')}
          loading={download.isPending && download.variables !== 'separate'}
        />
      </Dropdown>
    </Space.Compact>
  )
}
