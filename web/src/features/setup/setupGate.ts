/**
 * When the first-start wizard opens by itself (desktop app): once per session while no large model is found.
 * "Later" in the wizard, or having been shown once, stops it opening again until the app is opened again; the
 * dashboard keeps a notice with a link.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { getSetup } from '../../api/setup'
import { useDesktop } from '../../desktop'

const SHOWN = 'jacara.setupShown'

function shown(): boolean {
  try {
    return sessionStorage.getItem(SHOWN) === '1'
  } catch {
    return false
  }
}

/** The wizard was shown or put off: do not open it by itself again in this session. */
export function postponeSetup(): void {
  try {
    sessionStorage.setItem(SHOWN, '1')
  } catch {
    // Storage unavailable: the wizard may open again on the next page load.
  }
}

/** Whether a large model is still missing (desktop app); undefined while unknown or outside the desktop app. */
export function useNeedsModel(): boolean | undefined {
  const desktop = useDesktop()
  const setup = useQuery({ queryKey: ['setup', false], queryFn: () => getSetup(false), enabled: desktop, staleTime: 30_000 })
  return desktop ? setup.data?.needs_model : undefined
}

/** In the page frame: open the wizard by itself once, when no large model is found. */
export function useSetupRedirect(): void {
  const needsModel = useNeedsModel()
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    // Settings stay reachable (restoring a backup, moving the data folder).
    if (needsModel && !shown() && location.pathname !== '/setup' && !location.pathname.startsWith('/settings')) {
      postponeSetup()
      void navigate('/setup')
    }
  }, [needsModel, location.pathname, navigate])
}
