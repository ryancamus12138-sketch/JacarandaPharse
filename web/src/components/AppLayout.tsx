import { AuditOutlined, FileTextOutlined, LogoutOutlined, TeamOutlined } from '@ant-design/icons'
import { Button, Flex, Layout, Menu, type MenuProps, Tag, Typography } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'

/** 登录后所有页面共用的外框：左边菜单、顶上用户名和退出。中间的 <Outlet /> 放具体页面。 */
export function AppLayout() {
  const { me, can, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  // 菜单按权限显示：使用者看不到管理员的菜单
  const items: MenuProps['items'] = [
    { key: '/documents', icon: <FileTextOutlined />, label: '文档' },
    ...(can('user:manage')
      ? [{ key: '/admin/users', icon: <TeamOutlined />, label: '用户管理' }]
      : []),
    ...(can('audit:read')
      ? [{ key: '/admin/audit-logs', icon: <AuditOutlined />, label: '审计日志' }]
      : []),
  ]
  // 当前网址对应哪个菜单（/documents/123 也算"文档"）
  const selected = items
    .map((item) => String(item?.key))
    .filter((key) => location.pathname.startsWith(key))

  async function handleLogout() {
    await logout()
    void navigate('/login', { replace: true })
  }

  const name = me?.user ? me.user.display_name || me.user.username : '本机用户'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider theme="light" width={200} style={{ borderRight: '1px solid #f0f0f0' }}>
        <Typography.Title level={5} style={{ padding: '20px 24px 8px', margin: 0 }}>
          JacarandaParse
        </Typography.Title>
        <Menu
          mode="inline"
          items={items}
          selectedKeys={selected}
          onClick={({ key }) => void navigate(key)}
          style={{ borderInlineEnd: 'none' }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{ background: '#fff', borderBottom: '1px solid #f0f0f0', paddingInline: 24 }}
        >
          <Flex justify="end" align="center" gap={12} style={{ height: '100%' }}>
            <Typography.Text>{name}</Typography.Text>
            <Tag color={me?.role === 'admin' ? 'purple' : 'default'}>
              {me?.role === 'admin' ? '管理员' : '使用者'}
            </Tag>
            {me?.mode === 'team' ? (
              <Button icon={<LogoutOutlined />} onClick={() => void handleLogout()}>
                退出
              </Button>
            ) : (
              <Tag>个人模式</Tag>
            )}
          </Flex>
        </Layout.Header>
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
