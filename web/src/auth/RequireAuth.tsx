import { Button, Flex, Result, Spin } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from './useAuth'
import { useTranslation } from 'react-i18next'

/**
 * Gate: redirects to login when not logged in and shows an error when the backend is unreachable. Renders its
 * children only after login.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const { me, loading, error, refresh } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <Flex justify="center" style={{ padding: 96 }}>
        <Spin size="large" />
      </Flex>
    )
  }
  if (error) {
    return (
      <Result
        status="error"
        title={t('打不开')}
        subTitle={error.message}
        extra={<Button onClick={() => void refresh()}>{t('重试')}</Button>}
      />
    )
  }
  if (me === null) {
    // Remember the intended destination and return there after login.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}

/**
 * Page-level permission check: shows 403 without the permission. The backend still enforces every operation.
 */
export function RequirePermission({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { t } = useTranslation()
  const { can } = useAuth()
  if (!can(permission)) {
    return <Result status="403" title={t('没有权限')} subTitle={t('这个页面只有管理员能看。')} />
  }
  return children
}
