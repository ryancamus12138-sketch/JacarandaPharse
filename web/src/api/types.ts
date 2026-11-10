/** 后端返回的数据长什么样。和 backend 的 schemas.py 一一对应，字段名保持一样。 */

export interface Health {
  status: string
  version: string
}

export interface User {
  id: string
  username: string
  display_name: string
  role: string
  permissions: string[]
  is_active: boolean
  created_at: string
}

/** GET /auth/me：现在是谁、能做什么。 */
export interface Me {
  mode: 'personal' | 'team'
  role: string
  permissions: string[]
  user: User | null
}

/** POST /auth/login 的返回。token 只在这里出现一次。 */
export interface LoginOut {
  token: string
  expires_at: string
  user: User
}
