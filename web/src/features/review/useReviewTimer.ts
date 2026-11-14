import { useEffect } from 'react'

import { getToken } from '../../api/token'

/**
 * Seconds without mouse or keyboard input after which the reviewer is considered away; that time is not
 * counted.
 */
const IDLE_SECONDS = 60
/** Report interval (seconds): frequent reports lose only a short span if the browser closes abruptly. */
const FLUSH_SECONDS = 30

/**
 * Review time: seconds with the review page open and in use, accumulated and reported to the backend every 30
 * seconds and once more on leaving.
 *
 * - Time in a background tab or minimized window is excluded;
 * - One minute without mouse, keyboard or wheel input is excluded (the reviewer is away);
 * - Reports on close use keepalive so they are delivered even as the browser closes.
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
      }).catch(() => undefined) // Failures to record time never interrupt the review.
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
