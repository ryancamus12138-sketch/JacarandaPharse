/**
 * Single entry point for backend calls. Every page requests /api/v1 through this module.
 * Use api() for JSON responses and apiBlob() for files (images, Excel).
 *
 * - The Authorization header is added automatically when a token exists;
 * - Backend errors return {"detail": "..."}, which is converted to ApiError so pages can display message
 *   directly;
 * - A 401 despite a token (expired, deactivated, logged out) clears the token and notifies AuthProvider to
 *   return to the login page.
 */

import { clearToken, getToken } from './token'
import { t, uiLanguage } from '../i18n'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Same as fetch options, plus json: data sent to the backend, serialized to JSON automatically. */
export interface ApiInit extends RequestInit {
  json?: unknown
}

let onUnauthorized: (() => void) | null = null

/**
 * Called by AuthProvider: what to do when the session becomes invalid (clear the current user, return to
 * login).
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

/**
 * Send a request: attach the token and handle 401 and errors. Returns the raw Response on success; the
 * functions below decide how to read it.
 */
async function send(path: string, init: ApiInit): Promise<Response> {
  const { json, ...rest } = init
  const headers = new Headers(rest.headers)
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (json !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  // The backend localizes messages per request.
  headers.set('Accept-Language', uiLanguage())

  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      ...rest,
      headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
  } catch {
    // The request was never sent: the backend is not running or the network is down.
    throw new ApiError(0, t('无法连接后端。请确认终端中 uv run jacara api 正在运行'))
  }

  if (response.status === 401 && token) {
    // 401 despite a token: the token is invalid. A 401 without a token (such as a wrong login password) does
    // not count.
    clearToken()
    onUnauthorized?.()
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response))
  }
  return response
}

/** Call an endpoint returning JSON (most endpoints). */
export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const response = await send(path, init)
  if (response.status === 204) {
    return undefined as T // 204: success without content (for example logout).
  }
  return (await response.json()) as T
}

/** Call an endpoint returning a file (images, Excel). */
export async function apiBlob(path: string, init: ApiInit = {}): Promise<Blob> {
  const response = await send(path, init)
  return response.blob()
}

/** A downloaded file: content + file name supplied by the backend. */
export interface DownloadedFile {
  blob: Blob
  filename: string
}

/**
 * Call a download endpoint (Excel export): besides the content, the file name from Content-Disposition is
 * needed.
 */
export async function apiDownload(path: string, fallbackName: string): Promise<DownloadedFile> {
  const response = await send(path, {})
  const filename = filenameFrom(response.headers.get('Content-Disposition')) ?? fallbackName
  return { blob: await response.blob(), filename }
}

/**
 * File name from Content-Disposition. The backend supplies both forms (RFC 6266):
 * filename*=UTF-8''%E6%94%B6%E6%8D%AE.xlsx (percent-encoded per the standard) takes precedence, then
 * filename="export.xlsx".
 */
function filenameFrom(header: string | null): string | null {
  if (!header) {
    return null
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    return decodeURIComponent(encoded[1])
  }
  return /filename="?([^";]+)"?/i.exec(header)?.[1] ?? null
}

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 502 || response.status === 504) {
    return t('无法连接后端。请确认终端中 uv run jacara api 正在运行')
  }
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') {
      return body.detail
    }
    if (Array.isArray(body.detail)) {
      // 422: FastAPI validation errors come as a list; use the first entry.
      const first = body.detail[0] as { msg?: string } | undefined
      return first?.msg ?? t('提交内容的格式不正确')
    }
  } catch {
    // Not JSON: fall back to the generic message below.
  }
  return t('请求失败（{{status}}）', { status: response.status })
}
