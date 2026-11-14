/**
 * The login token is kept in the browser (localStorage), so it survives reloads and new tabs.
 * Tokens expire on the backend after 12 hours; requests then return 401 and the user is sent to the login
 * page.
 *
 * When the browser forbids storage (some privacy settings), reads and writes throw; errors are swallowed here
 * so the page does not crash.
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
    // Storage unavailable: a rare browser setting; login will not persist.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    // Same as above.
  }
}
