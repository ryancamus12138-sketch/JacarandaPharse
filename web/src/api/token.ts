/**
 * 登录 token 存在浏览器里（localStorage），刷新页面、开新标签页都还在。
 * token 12 小时后在后端过期，到时候请求会 401，自动回到登录页。
 *
 * 浏览器禁止存储时（比如某些隐私设置）读写会出错，这里吞掉错误，免得整个网页崩掉。
 */

const KEY = 'jacara.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token)
  } catch {
    // 存不了：这种浏览器设置很少见，登录会不生效
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // 同上
  }
}
