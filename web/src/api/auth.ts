/** Authentication API: /api/v1/auth. */

import { api, apiBlob } from './client'
import type { LoginOut, Me, Profile } from './types'

export function login(username: string, password: string): Promise<LoginOut> {
  return api<LoginOut>('/auth/login', { method: 'POST', json: { username, password } })
}

export function logout(): Promise<void> {
  return api<void>('/auth/logout', { method: 'POST' })
}

export function getMe(): Promise<Me> {
  return api<Me>('/auth/me')
}

/**
 * Change one's own password; the current password is required. Other devices are logged out; the current
 * session stays.
 */
export function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  return api<void>('/auth/password', {
    method: 'POST',
    json: { old_password: oldPassword, new_password: newPassword },
  })
}

/** Forgotten password: user name + reset code from an administrator + new password. No login required. */
export function resetWithCode(username: string, code: string, password: string): Promise<void> {
  return api<void>('/auth/reset-password', {
    method: 'POST',
    json: { username, code, password },
  })
}

/** Own profile. */
export function getProfile(): Promise<Profile> {
  return api<Profile>('/auth/profile')
}

/** Change the display name. An empty string removes it (the user name is shown). */
export function updateProfile(displayName: string): Promise<Profile> {
  return api<Profile>('/auth/profile', { method: 'PATCH', json: { display_name: displayName } })
}

/** Own avatar (256 × 256 PNG). */
export function getAvatar(): Promise<Blob> {
  return apiBlob('/auth/profile/avatar')
}

/** Change the avatar: PNG, JPG or WEBP, up to 5 MB. The backend crops it to a square. */
export function uploadAvatar(file: File): Promise<Profile> {
  const form = new FormData()
  form.append('file', file)
  return api<Profile>('/auth/profile/avatar', { method: 'PUT', body: form })
}

export function removeAvatar(): Promise<Profile> {
  return api<Profile>('/auth/profile/avatar', { method: 'DELETE' })
}
