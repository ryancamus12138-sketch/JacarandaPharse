import { App } from 'antd'
import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import * as authApi from '../api/auth'
import { ApiError, setUnauthorizedHandler } from '../api/client'
import { clearToken, setToken } from '../api/token'
import type { Me } from '../api/types'
import { AuthContext, type AuthState } from './context'

/** 包在整个网站外面：打开网页时问一次后端"我是谁"，结果放进 AuthContext 给所有页面用。 */
export function AuthProvider({ children }: { children: ReactNode }) {
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
    // 网页打开时问一次。ignore：页面已经关掉了就不再更新（StrictMode 下会执行两次）
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
    // 用着用着 token 失效了（过期、被管理员停用）：清掉"我是谁"，RequireAuth 会带去登录页
    setUnauthorizedHandler(() => {
      setMe(null)
      // key：几个请求同时失效时，只显示一条提示
      void message.open({
        key: 'session-expired',
        type: 'warning',
        content: '登录已失效，请重新登录',
      })
    })
    return () => setUnauthorizedHandler(null)
  }, [message])

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
      // 后端连不上也照样在本地退出
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

/** 问后端"我是谁"。401 = 团队模式还没登录，这是正常情况，不算错误。 */
async function loadMe(): Promise<{ me: Me | null; error: ApiError | null }> {
  try {
    return { me: await authApi.getMe(), error: null }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      return { me: null, error: null }
    }
    return { me: null, error: err instanceof ApiError ? err : new ApiError(0, String(err)) }
  }
}
