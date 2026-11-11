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

/** 自己改密码：要先输原密码。别的设备上的登录会退出，当前这个不退出。 */
export function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  return api<void>('/auth/password', {
    method: 'POST',
    json: { old_password: oldPassword, new_password: newPassword },
  })
}

/** 忘记密码：用户名 + 管理员给的重设码 + 新密码。不用登录。 */
export function resetWithCode(username: string, code: string, password: string): Promise<void> {
  return api<void>('/auth/reset-password', {
    method: 'POST',
    json: { username, code, password },
  })
}
