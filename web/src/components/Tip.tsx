import { Alert } from 'antd'
import { type CSSProperties, type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'

const PREFIX = 'jacara.tip.'

function closed(id: string): boolean {
  try {
    return localStorage.getItem(PREFIX + id) === '1'
  } catch {
    return false
  }
}

/**
 * Blue help tip with a close button in the top-right corner; once closed it stays hidden (remembered in this
 * browser; reappears in another browser or after clearing site data).
 * Only for usage hints; do not use it for status messages such as "processing" or "processing failed".
 */
export function Tip({
  id,
  title,
  description,
  style,
}: {
  /** Name of each tip, used to remember that it was closed. */
  id: string
  title: ReactNode
  description?: ReactNode
  style?: CSSProperties
}) {
  const { t } = useTranslation()
  const [hidden, setHidden] = useState(() => closed(id))
  if (hidden) return null
  return (
    <Alert
      type="info"
      showIcon
      closable={{ 'aria-label': t('关掉这个提示') }}
      title={title}
      description={description}
      style={style}
      onClose={() => {
        setHidden(true)
        try {
          localStorage.setItem(PREFIX + id, '1')
        } catch {
          // Storage unavailable: hidden now, shown again next time.
        }
      }}
    />
  )
}
