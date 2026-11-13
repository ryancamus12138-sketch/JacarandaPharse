import { CheckOutlined, DownloadOutlined, DownOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, type ButtonProps, Dropdown, type MenuProps, Space } from 'antd'
import { ApiError } from '../../api/client'
import { type ExportMode, exportDocuments } from '../../api/documents'
import { saveFile } from '../../utils/download'
import { languageName, useLanguage } from '../../utils/languages'

const MAX_EXPORT = 100 // 和后端一样：一次最多导出 100 个

interface Props {
  ids: string[]
  type?: ButtonProps['type']
  /** 列名可以选的语言（ADR 0048），第一个是中文。不写：中文、英文。 */
  languages?: string[]
  /** 由外面管选的语言（审核页：和"字段名"共用，选了 English 导出也是 English）。不写就自己记一份。 */
  language?: string
  onLanguage?: (code: string) => void
}

const DEFAULT_LANGUAGES = ['zh', 'en']

/**
 * 导出按钮。
 * - 一个文档：直接下载这一份 Excel；
 * - 多个文档：主按钮下载 zip（每份一个 Excel），旁边的小箭头可以选"汇总到一个 Excel"。
 */
export function ExportButton({ ids, type, languages = DEFAULT_LANGUAGES, language: shown, onLanguage }: Props) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [own, setOwn] = useLanguage('export', languages)
  const language = shown ?? own
  const setLanguage = onLanguage ?? setOwn
  const download = useMutation({
    mutationFn: (mode: ExportMode) => exportDocuments(ids, mode, language),
    onSuccess: ({ blob, filename }) => {
      saveFile(blob, filename)
      void message.success(`已导出 ${filename}（导出记录里可以重新下载）`)
      void queryClient.invalidateQueries({ queryKey: ['exports'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const tooMany = ids.length > MAX_EXPORT
  const disabled = ids.length === 0 || tooMany
  const title = tooMany ? `一次最多导出 ${MAX_EXPORT} 个` : undefined

  const csv = { key: 'csv', label: '导出为 CSV' }
  // 列名用哪种语言：选了就记住，下次导出也用它；点了不导出
  const languageMenu: MenuProps['items'] =
    languages.length > 1
      ? [
          { type: 'divider' },
          {
            type: 'group',
            label: '列名语言',
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
      void message.info(`导出的列名改用${languageName(key.slice(5))}`)
      return
    }
    other()
  }
  const suffix = language !== (languages[0] ?? 'zh') ? `（${languageName(language)}）` : ''

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
          导出 Excel{suffix}
        </Button>
        <Dropdown
          disabled={disabled}
          placement="bottomRight"
          menu={{
            items: [csv, ...languageMenu],
            onClick: ({ key }) => onMenu(key, () => download.mutate('csv')),
          }}
        >
          <Button
            type={type}
            icon={<DownOutlined />}
            aria-label="更多导出格式"
            loading={download.isPending && download.variables === 'csv'}
          />
        </Dropdown>
      </Space.Compact>
    )
  }

  return (
    // Space.Compact：两个按钮紧贴在一起，看起来像一个
    <Space.Compact>
      <Button
        type={type}
        icon={<DownloadOutlined />}
        disabled={disabled}
        title={title}
        loading={download.isPending && download.variables === 'separate'}
        onClick={() => download.mutate('separate')}
      >
        导出 {ids.length} 份 Excel{suffix}
      </Button>
      <Dropdown
        disabled={disabled}
        placement="bottomRight"
        menu={{
          items: [{ key: 'summary', label: '汇总到一个 Excel' }, csv, ...languageMenu],
          onClick: ({ key }) => onMenu(key, () => download.mutate(key === 'csv' ? 'csv' : 'summary')),
        }}
      >
        <Button
          type={type}
          icon={<DownOutlined />}
          aria-label="更多导出格式"
          loading={download.isPending && download.variables !== 'separate'}
        />
      </Dropdown>
    </Space.Compact>
  )
}
