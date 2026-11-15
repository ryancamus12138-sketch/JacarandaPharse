/** User management API: /api/v1/users (administrators only). */

import { api } from './client'
import type { PermissionOption, ResetCode, User, UserCreate, UserUpdate } from './types'

export function listUsers(): Promise<User[]> {
  return api<User[]>('/users')
}

/** Grantable permissions, rendered as checkboxes. */
export function listPermissions(): Promise<PermissionOption[]> {
  return api<PermissionOption[]>('/users/permissions')
}

export function createUser(data: UserCreate): Promise<User> {
  return api<User>('/users', { method: 'POST', json: data })
}

/**
 * Change only the given fields: grant permissions, deactivate, change role or name. Takes effect immediately.
 */
export function updateUser(id: string, change: UserUpdate): Promise<User> {
  return api<User>(`/users/${id}`, { method: 'PATCH', json: change })
}

/**
 * Generate a one-time reset code for a user who forgot the password: valid for 15 minutes, usable once.
 * The administrator passes the code to the user, who sets a new password; the administrator never learns it.
 */
export function createResetCode(id: string): Promise<ResetCode> {
  return api<ResetCode>(`/users/${id}/reset-code`, { method: 'POST' })
}
