/** 用户管理的接口：/api/v1/users（都只有管理员能用）。 */

import { api } from './client'
import type { PermissionOption, ResetCode, User, UserCreate, UserUpdate } from './types'

export function listUsers(): Promise<User[]> {
  return api<User[]>('/users')
}

/** 可以开通的权限，用来显示勾选框。 */
export function listPermissions(): Promise<PermissionOption[]> {
  return api<PermissionOption[]>('/users/permissions')
}

export function createUser(data: UserCreate): Promise<User> {
  return api<User>('/users', { method: 'POST', json: data })
}

/** 只改给出的字段：开通权限、停用、改角色、改名字。改了马上生效。 */
export function updateUser(id: string, change: UserUpdate): Promise<User> {
  return api<User>(`/users/${id}`, { method: 'PATCH', json: change })
}

/**
 * 给忘了密码的人生成一次性重设码：15 分钟有效，只能用一次。
 * 管理员把码告诉本人，本人自己设新密码，管理员不知道新密码。
 */
export function createResetCode(id: string): Promise<ResetCode> {
  return api<ResetCode>(`/users/${id}/reset-code`, { method: 'POST' })
}
