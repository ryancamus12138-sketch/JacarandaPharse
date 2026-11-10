import { Button, Flex, Result, Spin } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from './useAuth'

/** 守门员：还没登录就带去登录页；后端连不上就显示错误。登录了才显示里面的页面。 */
export function RequireAuth({ children }: { children: ReactNode }) {
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
        title="打不开"
        subTitle={error.message}
        extra={<Button onClick={() => void refresh()}>重试</Button>}
      />
    )
  }
  if (me === null) {
    // 记下原来想去的地址，登录后再回去
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}

/** 页面级的权限检查：没有这个权限就显示 403。真正挡住操作的还是后端。 */
export function RequirePermission({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { can } = useAuth()
  if (!can(permission)) {
    return <Result status="403" title="没有权限" subTitle="这个页面只有管理员能看。" />
  }
  return children
}
