import { App } from 'antd'
import { useCallback, useState } from 'react'
import { ApiError } from '../../api/client'
import type { Job } from '../../api/desktop'
import { JobDialog } from './MaintenanceJob'

/**
 * Start a maintenance job and follow it in a dialog. After a successful restore or move the page reloads,
 * since every piece of data has changed; otherwise onFinished runs.
 */
export function useMaintenanceJob(onFinished?: (job: Job) => void) {
  const { message } = App.useApp()
  const [current, setCurrent] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const run = useCallback(
    async (start: () => Promise<Job>) => {
      setStarting(true)
      try {
        setCurrent(await start())
      } catch (err) {
        void message.error(err instanceof ApiError ? err.message : String(err))
      } finally {
        setStarting(false)
      }
    },
    [message],
  )
  const close = (job: Job) => {
    setCurrent(null)
    if (job.error === null && (job.kind === 'restore' || job.kind === 'move')) {
      window.location.reload()
      return
    }
    onFinished?.(job)
  }
  const dialog = current ? <JobDialog key={current.started} started={current} onClose={close} /> : null
  return { run, dialog, busy: starting || current !== null }
}
