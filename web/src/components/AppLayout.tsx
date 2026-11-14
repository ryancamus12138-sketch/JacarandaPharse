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
import { DesktopBridge } from './DesktopBridge'
import { LanguageSwitch } from './LanguageSwitch'
import { UserAvatar } from './UserAvatar'
import { useTranslation } from 'react-i18next'
import { useSetupRedirect } from '../features/setup/setupGate'

/**
 * Shared frame of all pages after login: sidebar menu, user name and logout at the top; <Outlet /> renders
 * the page.
 * Under /settings/…, the sidebar shows the settings sections, with a "Back" item on top returning to the page
 * visited before entering settings.
 */
export function AppLayout() {
  const { t } = useTranslation()
  const { me, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  // Narrow screens (below 992 pixels, such as split-screen laptops and tablets): the sidebar collapses and
  // opens from the button at the top left.
  const screens = Grid.useBreakpoint()
  const narrow = screens.lg === false
  const [collapsed, setCollapsed] = useState(true)
  const inSettings = location.pathname.startsWith('/settings')
  const sections = useSettingsSections()
  // Desktop app without a large model: the first-start wizard opens once by itself.
  useSetupRedirect()

  // Remember the page visited before entering settings.
  useEffect(() => {
    if (!location.pathname.startsWith('/settings')) {
      rememberPage(location.pathname + location.search)
    }
  }, [location.pathname, location.search])

  const items: MenuProps['items'] = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: t('工作台') },
    { key: '/documents', icon: <FileTextOutlined />, label: t('文档') },
    { key: '/templates', icon: <AppstoreOutlined />, label: t('模板') },
    { key: '/exports', icon: <ExportOutlined />, label: t('导出记录') },
    { key: '/insights', icon: <BarChartOutlined />, label: t('统计') },
    { key: '/ocr-samples', icon: <ScanOutlined />, label: t('OCR 样本') },
    { key: '/hub', icon: <DatabaseOutlined />, label: t('模型和数据集') },
    // User management and audit log live under Settings → Team and permissions (team edition only).
    { key: '/settings', icon: <SettingOutlined />, label: t('设置') },
  ]
  // Menu item matching the current URL (/documents/123 counts as "Documents").
  const selected = items
    .map((item) => String(item?.key))
    .filter((key) => location.pathname.startsWith(key))

  async function handleLogout() {
    await logout()
    void navigate('/login', { replace: true })
  }

  const name =
    me?.shown_name || (me?.user ? me.user.display_name || me.user.username : t('我'))
  const roleTag = (
    <Tag color={me?.role === 'admin' ? 'purple' : 'default'} style={{ marginInlineEnd: 0 }}>
      {me?.role === 'admin' ? t('管理员') : t('使用者')}
    </Tag>
  )
  function go(path: string) {
    void navigate(path)
    if (narrow) setCollapsed(true) // Narrow screens: collapse after selection.
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
        trigger={null} // On narrow screens the menu opens from the button at the left of the header.
        // On narrow screens the menu floats over the page instead of narrowing the content.
        style={{
          borderRight: '1px solid #f0f0f0',
          ...(narrow ? { position: 'fixed', height: '100vh', zIndex: 100 } : {}),
        }}
      >
        <Flex align="center" gap={8} style={{ padding: '20px 16px 8px' }}>
          {/* The icon in web/public, the same one shown in the browser tab. */}
          <img src="/icon-192.png" alt="" width={24} height={24} style={{ flexShrink: 0 }} />
          {/* nowrap: keep the name on one line. */}
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
              {t('返回')}
            </Button>
            <Typography.Title level={4} style={{ margin: '12px 24px 8px' }}>
              {t('设置')}
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
          {/* Avatar and display name, linking to the profile. */}
          <Flex
            role="button"
            tabIndex={0}
            aria-label={t('个人资料')}
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
              {/* Personal mode has one user and no roles. */}
              {me?.mode === 'team' && <span>{roleTag}</span>}
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
                aria-label={t('菜单')}
                onClick={() => setCollapsed((c) => !c)}
                style={{ marginInlineEnd: 'auto' }}
              />
            )}
            {/*
             * On wide screens the sidebar already shows avatar and name; only when the sidebar is collapsed
             * on narrow screens is an avatar shown here.
             */}
            {narrow && (
              <Button
                type="text"
                shape="circle"
                onClick={openProfile}
                aria-label={t('个人资料')}
                style={{ width: 36, height: 36, padding: 0 }}
              >
                <UserAvatar size={28} />
              </Button>
            )}
            <LanguageSwitch />
            {me?.mode === 'team' ? (
              <Button icon={<LogoutOutlined />} onClick={() => void handleLogout()}>
                {t('退出')}
              </Button>
            ) : (
              <Tag>{t('个人版')}</Tag>
            )}
          </Flex>
        </Layout.Header>
        <Layout.Content style={{ padding: narrow ? 12 : 24 }}>
          <DesktopBridge />
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
