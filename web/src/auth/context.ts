/** 全站共用的"现在是谁"。任何页面用 useAuth() 就能拿到。 */

import { createContext } from 'react'
import type { ApiError } from '../api/client'
import type { Me } from '../api/types'

export interface AuthState {
  /** 现在是谁。null：还没登录（团队模式）。 */
  me: Me | null
  /** 第一次问后端"我是谁"还没回来。 */
  loading: boolean
  /** 问不到：后端没启动之类。401 不算错误，算"没登录"。 */
  error: ApiError | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** 重新问一次后端"我是谁"。 */
  refresh: () => Promise<void>
  /** 有没有某个权限，比如 can('user:manage')。 */
  can: (permission: string) => boolean
}

export const AuthContext = createContext<AuthState | null>(null)
