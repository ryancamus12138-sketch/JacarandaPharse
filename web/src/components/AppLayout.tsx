import {
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExportOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  LogoutOutlined,
  MenuOutlined,
  ScanOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button, Flex, Grid, Layout, Menu, type MenuProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { UserAvatar } from './UserAvatar'

/** 登录后所有页面共用的外框：左边菜单、顶上用户名和退出。中间的 <Outlet /> 放具体页面。 */
export function AppLayout() {
  const { me, can, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  // 窄屏（小于 992 像素，比如笔记本分屏、平板）：左边菜单收起，点左上角的小按钮再打开
  const screens = Grid.useBreakpoint()
  const narrow = screens.lg === false
  const [collapsed, setCollapsed] = useState(true)

  // 菜单按权限显示：使用者看不到管理员的菜单
  const items: MenuProps['items'] = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: '工作台' },
    { key: '/documents', icon: <FileTextOutlined />, label: '文档' },
    { key: '/templates', icon: <AppstoreOutlined />, label: '模板' },
    { key: '/exports', icon: <ExportOutlined />, label: '导出记录' },
    { key: '/insights', icon: <BarChartOutlined />, label: '统计' },
    { key: '/ocr-samples', icon: <ScanOutlined />, label: 'OCR 样本' },
    { key: '/hub', icon: <DatabaseOutlined />, label: '模型和数据集' },
    { key: '/system', icon: <InfoCircleOutlined />, label: '系统信息' },
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

  const name =
    me?.shown_name || (me?.user ? me.user.display_name || me.user.username : '本机用户')
  const roleTag = (
    <Tag color={me?.role === 'admin' ? 'purple' : 'default'} style={{ marginInlineEnd: 0 }}>
      {me?.role === 'admin' ? '管理员' : '使用者'}
    </Tag>
  )
  function openProfile() {
    void navigate('/profile')
    if (narrow) setCollapsed(true)
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider
        theme="light"
        width={216}
        breakpoint="lg"
        collapsedWidth={0}
        collapsed={narrow && collapsed}
        onCollapse={setCollapsed}
        trigger={null} // 窄屏时用顶栏左边的按钮打开菜单
        // 窄屏时菜单浮在页面上面，不把内容挤窄
        style={{
          borderRight: '1px solid #f0f0f0',
          ...(narrow ? { position: 'fixed', height: '100vh', zIndex: 100 } : {}),
        }}
      >
        <Flex align="center" gap={8} style={{ padding: '20px 16px 8px' }}>
          {/* 图标在 web/public 里，和浏览器标签页上的是同一个 */}
          <img src="/icon-192.png" alt="" width={24} height={24} style={{ flexShrink: 0 }} />
          {/* nowrap：名字不换行 */}
          <Typography.Title level={5} style={{ margin: 0, whiteSpace: 'nowrap' }}>
            JacarandaParse
          </Typography.Title>
        </Flex>
        {/* 头像和昵称：点进去是个人资料 */}
        <Flex
          role="button"
          tabIndex={0}
          aria-label="个人资料"
          align="center"
          gap={10}
          onClick={openProfile}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') openProfile()
          }}
          style={{
            margin: '8px 12px 4px',
            padding: '8px',
            borderRadius: 8,
            cursor: 'pointer',
            background: location.pathname === '/profile' ? '#f4f0fb' : undefined,
          }}
        >
          <UserAvatar size={36} />
          <Flex vertical style={{ minWidth: 0 }}>
            <Typography.Text strong ellipsis style={{ maxWidth: 140 }}>
              {name}
            </Typography.Text>
            <span>{roleTag}</span>
          </Flex>
        </Flex>
        <Menu
          mode="inline"
          items={items}
          selectedKeys={selected}
          onClick={({ key }) => {
            void navigate(key)
            if (narrow) setCollapsed(true) // 窄屏：选好就收起来
          }}
          style={{ borderInlineEnd: 'none' }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: '#fff',
            borderBottom: '1px solid #f0f0f0',
            paddingInline: narrow ? 12 : 24,
          }}
        >
          <Flex justify="end" align="center" gap={12} style={{ height: '100%' }}>
            {narrow && (
              <Button
                type="text"
                icon={<MenuOutlined />}
                aria-label="菜单"
                onClick={() => setCollapsed((c) => !c)}
                style={{ marginInlineEnd: 'auto' }}
              />
            )}
            {/* 宽屏时左边菜单顶上已经有头像和昵称，这里不重复；窄屏菜单收起了，才在这里放一个头像 */}
            {narrow && (
              <Button
                type="text"
                shape="circle"
                onClick={openProfile}
                aria-label="个人资料"
                style={{ width: 36, height: 36, padding: 0 }}
              >
                <UserAvatar size={28} />
              </Button>
            )}
            {me?.mode === 'team' ? (
              <Button icon={<LogoutOutlined />} onClick={() => void handleLogout()}>
                退出
              </Button>
            ) : (
              <Tag>个人模式</Tag>
            )}
          </Flex>
        </Layout.Header>
        <Layout.Content style={{ padding: narrow ? 12 : 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
