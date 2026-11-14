/** App-wide current user. Any page can read it with useAuth(). */

import { createContext } from 'react'
import type { ApiError } from '../api/client'
import type { Me } from '../api/types'

export interface AuthState {
  /** Current user. null: not logged in (team mode). */
  me: Me | null
  /** The first request for the current user has not returned yet. */
  loading: boolean
  /** Request failed: backend not running or similar. 401 is not an error but means not logged in. */
  error: ApiError | null
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** Ask the backend for the current user again. */
  refresh: () => Promise<void>
  /** Whether a permission is granted, for example can('user:manage'). */
  can: (permission: string) => boolean
}

export const AuthContext = createContext<AuthState | null>(null)
