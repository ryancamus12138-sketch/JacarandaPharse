/**
 * 调用后端的唯一入口。所有页面都通过这里请求 /api/v1（项目规则 8）。
 * 返回 JSON 的用 api()，返回文件（图片、Excel）的用 apiBlob()。
 *
 * - 有 token 就自动带上 Authorization 头；
 * - 后端出错时返回 {"detail": "..."}，这里统一变成 ApiError，页面直接显示 message；
 * - 带着 token 却收到 401（过期、被停用、退出过），清掉 token 并通知 AuthProvider 回登录页。
 */

import { clearToken, getToken } from './token'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 和 fetch 的参数一样，多一个 json：要发给后端的数据，会自动变成 JSON。 */
export interface ApiInit extends RequestInit {
  json?: unknown
}

let onUnauthorized: (() => void) | null = null

/** AuthProvider 调用：登录失效时要做什么（清掉"我是谁"、回登录页）。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

/** 发请求：带 token、处理 401 和错误。成功时返回原始的 Response，由下面两个函数决定怎么读。 */
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

  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      ...rest,
      headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
  } catch {
    // 连请求都没发出去：后端没启动，或者网络断了
    throw new ApiError(0, '连不上后端。请确认终端里 uv run jacara api 正在运行')
  }

  if (response.status === 401 && token) {
    // 带了 token 还是 401：token 失效了。没带 token 的 401（比如登录密码错）不算
    clearToken()
    onUnauthorized?.()
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response))
  }
  return response
}

/** 调用返回 JSON 的接口（绝大多数接口）。 */
export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const response = await send(path, init)
  if (response.status === 204) {
    return undefined as T // 204：成功了，但没有内容（比如退出登录）
  }
  return (await response.json()) as T
}

/** 调用返回文件的接口（图片、Excel）。 */
export async function apiBlob(path: string, init: ApiInit = {}): Promise<Blob> {
  const response = await send(path, init)
  return response.blob()
}

/** 下载回来的文件：内容 + 后端给的文件名。 */
export interface DownloadedFile {
  blob: Blob
  filename: string
}

/** 调用"下载文件"的接口（导出 Excel）：除了内容，还要后端在 Content-Disposition 里给的文件名。 */
export async function apiDownload(path: string, fallbackName: string): Promise<DownloadedFile> {
  const response = await send(path, {})
  const filename = filenameFrom(response.headers.get('Content-Disposition')) ?? fallbackName
  return { blob: await response.blob(), filename }
}

/**
 * 从 Content-Disposition 里取文件名。后端两种都给（第十七课）：
 * filename*=UTF-8''%E6%94%B6%E6%8D%AE.xlsx（中文按标准编码过）优先，没有再用 filename="export.xlsx"。
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
    return '连不上后端。请确认终端里 uv run jacara api 正在运行'
  }
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') {
      return body.detail
    }
    if (Array.isArray(body.detail)) {
      // 422：FastAPI 的格式检查错误，是一个列表，取第一条
      const first = body.detail[0] as { msg?: string } | undefined
      return first?.msg ?? '提交的内容格式不对'
    }
  } catch {
    // 返回的不是 JSON，用下面的通用说法
  }
  return `请求失败（${response.status}）`
}
