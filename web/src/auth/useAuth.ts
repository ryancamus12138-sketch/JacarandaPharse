import { useContext } from 'react'
import { AuthContext, type AuthState } from './context'
import { useTranslation } from 'react-i18next'

/** Current user and permitted operations, from any page. Must be used inside <AuthProvider>. */
export function useAuth(): AuthState {
  const { t } = useTranslation()
  const auth = useContext(AuthContext)
  if (auth === null) {
    throw new Error(t('useAuth 必须放在 <AuthProvider> 里面使用'))
  }
  return auth
}
