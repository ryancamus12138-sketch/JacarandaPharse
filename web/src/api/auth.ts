/** 登录相关的接口：/api/v1/auth。 */

import { api } from './client'
import type { LoginOut, Me } from './types'

export function login(username: string, password: string): Promise<LoginOut> {
  return api<LoginOut>('/auth/login', { method: 'POST', json: { username, password } })
}

export function logout(): Promise<void> {
  return api<void>('/auth/logout', { method: 'POST' })
}

export function getMe(): Promise<Me> {
  return api<Me>('/auth/me')
}
