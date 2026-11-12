import { Alert } from 'antd'
import { type CSSProperties, type ReactNode, useState } from 'react'

const PREFIX = 'jacara.tip.'

function closed(id: string): boolean {
  try {
    return localStorage.getItem(PREFIX + id) === '1'
  } catch {
    return false
  }
}

/**
 * 蓝色的说明提示：右上角有 ×，点了就不再显示（记在这个浏览器里，换浏览器或清了网站数据会再出现）。
 * 只用于"说明怎么用"的提示；"正在处理""处理失败"这类状态提醒不要用它。
 */
export function Tip({
  id,
  title,
  description,
  style,
}: {
  /** 每个提示一个名字，记住"关掉了"用。 */
  id: string
  title: ReactNode
  description?: ReactNode
  style?: CSSProperties
}) {
  const [hidden, setHidden] = useState(() => closed(id))
  if (hidden) return null
  return (
    <Alert
      type="info"
      showIcon
      closable={{ 'aria-label': '关掉这个提示' }}
      title={title}
      description={description}
      style={style}
      onClose={() => {
        setHidden(true)
        try {
          localStorage.setItem(PREFIX + id, '1')
        } catch {
          // 存不了：这次关掉，下次打开还会出现
        }
      }}
    />
  )
}
