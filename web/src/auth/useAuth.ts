import { useContext } from 'react'
import { AuthContext, type AuthState } from './context'

/** 在任何页面里拿到"现在是谁、能做什么"。必须在 <AuthProvider> 里面用。 */
export function useAuth(): AuthState {
  const auth = useContext(AuthContext)
  if (auth === null) {
    throw new Error('useAuth 必须放在 <AuthProvider> 里面使用')
  }
  return auth
}
