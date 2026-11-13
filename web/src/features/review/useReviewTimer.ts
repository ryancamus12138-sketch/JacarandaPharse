import { useEffect } from 'react'

import { getToken } from '../../api/token'

/** 人多久没动鼠标、键盘就算走开了（秒）：走开的时间不算审核用时。 */
const IDLE_SECONDS = 60
/** 多久报一次（秒）：报得勤一点，浏览器突然关掉也只丢这一小段。 */
const FLUSH_SECONDS = 30

/**
 * 审核用时：审核页开着、人在操作的秒数，攒着每半分钟报给后端一次，离开页面时再报一次。
 *
 * - 页面切到后台（换了标签页、最小化）不算；
 * - 一分钟没动鼠标、键盘、滚轮也不算（人走开了）；
 * - 关页面时用 keepalive 发，浏览器关了也能发出去。
 */
export function useReviewTimer(documentId: string | undefined, enabled: boolean): void {
  useEffect(() => {
    if (!documentId || !enabled) return
    let seconds = 0
    let lastActive = Date.now()

    const flush = () => {
      const send = Math.min(seconds, 600)
      if (send < 1) return
      seconds = 0
      const token = getToken()
      void fetch(`/api/v1/documents/${documentId}/review-time`, {
        method: 'POST',
        keepalive: true,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ seconds: send }),
      }).catch(() => undefined) // 记用时失败不打扰人审核
    }
    const active = () => {
      lastActive = Date.now()
    }
    const tick = window.setInterval(() => {
      const away = Date.now() - lastActive > IDLE_SECONDS * 1000
      if (globalThis.document.visibilityState === 'visible' && !away) seconds += 1
    }, 1000)
    const every = window.setInterval(flush, FLUSH_SECONDS * 1000)
    const onVisibility = () => {
      if (globalThis.document.visibilityState === 'hidden') flush()
      else active()
    }
    const events = ['mousemove', 'keydown', 'wheel', 'click', 'touchstart'] as const
    for (const e of events) window.addEventListener(e, active, { passive: true })
    globalThis.document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', flush)
    return () => {
      window.clearInterval(tick)
      window.clearInterval(every)
      for (const e of events) window.removeEventListener(e, active)
      globalThis.document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [documentId, enabled])
}
