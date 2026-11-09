/**
 * 调用后端的唯一入口。所有页面都通过这里请求 /api/v1（项目规则 8）。
 *
 * 后端出错时返回 {"detail": "..."}，这里统一变成 ApiError，页面直接显示 message。
 */

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, init)
  } catch {
    // 连请求都没发出去：后端没启动，或者网络断了
    throw new ApiError(0, '连不上后端。请确认终端里 uv run jacara api 正在运行')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response))
  }
  if (response.status === 204) {
    return undefined as T // 204：成功了，但没有内容（比如退出登录）
  }
  return (await response.json()) as T
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
