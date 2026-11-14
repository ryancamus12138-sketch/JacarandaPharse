import {
  AppstoreOutlined,
  ArrowLeftOutlined,
  BarChartOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExportOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MenuOutlined,
  ScanOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { Button, Flex, Grid, Layout, Menu, type MenuProps, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { lastPage, rememberPage, useSettingsSections } from '../features/settings/sections'
import { UserAvatar } from './UserAvatar'

/**
 * 登录后所有页面共用的外框：左边菜单、顶上用户名和退出。中间的 <Outlet /> 放具体页面。
 * 进了设置（/settings/…），左边栏换成设置的几类，最上面一个"返回"回到进设置之前的那一页（ADR 0063）。
 */
export function AppLayout() {
  const { me, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  // 窄屏（小于 992 像素，比如笔记本分屏、平板）：左边菜单收起，点左上角的小按钮再打开
  const screens = Grid.useBreakpoint()
  const narrow = screens.lg === false
  const [collapsed, setCollapsed] = useState(true)
  const inSettings = location.pathname.startsWith('/settings')
  const sections = useSettingsSections()

  // 记下进设置之前在哪一页
  useEffect(() => {
    if (!location.pathname.startsWith('/settings')) {
      rememberPage(location.pathname + location.search)
    }
  }, [location.pathname, location.search])

  const items: MenuProps['items'] = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: '工作台' },
    { key: '/documents', icon: <FileTextOutlined />, label: '文档' },
    { key: '/templates', icon: <AppstoreOutlined />, label: '模板' },
    { key: '/exports', icon: <ExportOutlined />, label: '导出记录' },
    { key: '/insights', icon: <BarChartOutlined />, label: '统计' },
    { key: '/ocr-samples', icon: <ScanOutlined />, label: 'OCR 样本' },
    { key: '/hub', icon: <DatabaseOutlined />, label: '模型和数据集' },
    // 用户管理、审计日志在 设置 → 团队和权限 里（只有团队版有，ADR 0063）
    { key: '/settings', icon: <SettingOutlined />, label: '设置' },
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
  function go(path: string) {
    void navigate(path)
    if (narrow) setCollapsed(true) // 窄屏：选好就收起来
  }

  function openProfile() {
    void navigate('/settings/personal')
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
        {inSettings ? (
          <>
            <Button
              type="text"
              icon={<ArrowLeftOutlined />}
              onClick={() => go(lastPage())}
              style={{ margin: '8px 12px 0', justifyContent: 'flex-start', width: 'calc(100% - 24px)' }}
            >
              返回
            </Button>
            <Typography.Title level={4} style={{ margin: '12px 24px 8px' }}>
              设置
            </Typography.Title>
            <Menu
              mode="inline"
              items={sections.map((s) => ({ key: `/settings/${s.key}`, icon: s.icon, label: s.label }))}
              selectedKeys={[location.pathname]}
              onClick={({ key }) => go(key)}
              style={{ borderInlineEnd: 'none' }}
            />
          </>
        ) : (
          <>
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
              background: location.pathname === '/settings/personal' ? '#f4f0fb' : undefined,
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
            onClick={({ key }) => go(key)}
            style={{ borderInlineEnd: 'none' }}
          />
          </>
        )}
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
              <Tag>个人版</Tag>
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
