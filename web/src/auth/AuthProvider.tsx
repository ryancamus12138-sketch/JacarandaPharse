import { App } from 'antd'
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import * as authApi from '../api/auth'
import { ApiError, setUnauthorizedHandler } from '../api/client'
import { clearToken, setToken } from '../api/token'
import type { Me } from '../api/types'
import { AuthContext, type AuthState } from './context'
import { setDesktop } from '../desktop'
import { useTranslation } from 'react-i18next'

/**
 * Wraps the whole app: asks the backend for the current user once on load and provides the result to all
 * pages through AuthContext.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)

  const refresh = useCallback(async () => {
    const result = await loadMe()
    setMe(result.me)
    setError(result.error)
    setLoading(false)
  }, [])

  useEffect(() => {
    // Asked once on load. ignore: skip updates after unmount (effects run twice under StrictMode).
    let ignore = false
    void loadMe().then((result) => {
      if (!ignore) {
        setMe(result.me)
        setError(result.error)
        setLoading(false)
      }
    })
    return () => {
      ignore = true
    }
  }, [])

  useEffect(() => {
    // The token became invalid during use (expired, deactivated by an administrator): clear the current user;
    // RequireAuth redirects to login.
    setUnauthorizedHandler(() => {
      setMe(null)
      // key: show a single notification when several requests fail at once.
      void message.open({
        key: 'session-expired',
        type: 'warning',
        content: t('登录已失效，请重新登录'),
      })
    })
    return () => setUnauthorizedHandler(null)
  }, [message, t])

  const login = useCallback(
    async (username: string, password: string) => {
      const result = await authApi.login(username, password)
      setToken(result.token)
      await refresh()
    },
    [refresh],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // Log out locally even when the backend is unreachable.
    }
    clearToken()
    setMe(null)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      error,
      login,
      logout,
      refresh,
      can: (permission) => me?.permissions.includes(permission) ?? false,
    }),
    [me, loading, error, login, logout, refresh],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}

/**
 * Ask the backend for the current user. 401 means not logged in yet in team mode, which is expected and not
 * an error.
 */
async function loadMe(): Promise<{ me: Me | null; error: ApiError | null }> {
  try {
    const me = await authApi.getMe()
    setDesktop(me.desktop ?? false)
    return { me, error: null }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      return { me: null, error: null }
    }
    return { me: null, error: err instanceof ApiError ? err : new ApiError(0, String(err)) }
  }
}
