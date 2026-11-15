import { FolderOpenOutlined } from '@ant-design/icons'
import { App, Button } from 'antd'
import { useEffect } from 'react'
import { api, ApiError } from '../api/client'
import { useDesktop } from '../desktop'
import { SAVED_EVENT, type SavedFile } from '../utils/download'
import { useTranslation } from 'react-i18next'

/**
 * Desktop app only, mounted once in the page frame:
 * - announces each download saved to the Downloads folder, with a button that shows it in Finder or File
 *   Explorer;
 * - opens links to other web sites in the default browser: inside the app window they would replace the app
 *   with a page that has no way back.
 */
export function DesktopBridge() {
  const { t } = useTranslation()
  const { notification, message } = App.useApp()
  const desktop = useDesktop()

  useEffect(() => {
    if (!desktop) return
    const onSaved = (event: Event) => {
      const saved = (event as CustomEvent<SavedFile>).detail
      const key = `saved-${saved.path}`
      notification.success({
        key,
        title: t('已保存到“下载”文件夹'),
        description: saved.name,
        actions: (
          <Button
            size="small"
            icon={<FolderOpenOutlined />}
            onClick={() => {
              notification.destroy(key)
              api('/desktop/reveal', { method: 'POST', json: { path: saved.path } }).catch((err: unknown) =>
                message.error(err instanceof ApiError ? err.message : String(err)),
              )
            }}
          >
            {t('在文件夹中显示')}
          </Button>
        ),
      })
    }
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return
      const link = (event.target as Element | null)?.closest?.('a[href]')
      if (!(link instanceof HTMLAnchorElement)) return
      const url = new URL(link.href, window.location.href)
      if (url.origin === window.location.origin || !['http:', 'https:'].includes(url.protocol)) return
      event.preventDefault()
      api('/desktop/open-url', { method: 'POST', json: { url: url.href } }).catch((err: unknown) =>
        message.error(err instanceof ApiError ? err.message : String(err)),
      )
    }
    window.addEventListener(SAVED_EVENT, onSaved)
    // Capture phase: runs before any handler that would follow the link.
    document.addEventListener('click', onClick, true)
    return () => {
      window.removeEventListener(SAVED_EVENT, onSaved)
      document.removeEventListener('click', onClick, true)
    }
  }, [desktop, notification, message, t])

  return null
}
