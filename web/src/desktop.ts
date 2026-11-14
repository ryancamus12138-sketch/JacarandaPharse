import { useAuth } from './auth/useAuth'

/**
 * Whether the page is served by the desktop app (from the current user, /auth/me). Advice then names actions
 * in the app, such as restarting JacarandaParse, instead of terminal commands.
 */
let desktop = false

/** Remember how the backend runs; called whenever the current user is loaded. */
export function setDesktop(value: boolean): void {
  desktop = value
}

/** For messages built outside components (connection errors). */
export function isDesktop(): boolean {
  return desktop
}

/** In components: whether the page is served by the desktop app. */
export function useDesktop(): boolean {
  return useAuth().me?.desktop ?? false
}
