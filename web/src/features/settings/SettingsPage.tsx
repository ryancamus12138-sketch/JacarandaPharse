import { Tabs, Typography } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router'
import { useAuth } from '../../auth/useAuth'
import { AuditLogPage } from '../admin/AuditLogPage'
import { UsersPage } from '../admin/UsersPage'
import { ProfilePage } from '../profile/ProfilePage'
import { AboutSettings } from './AboutSettings'
import { DataSettings } from './DataSettings'
import { ModelsCard } from './ModelsCard'
import { ProcessingSettings } from './ProcessingSettings'
import { useSettingsSections } from './sections'

function TeamSettings() {
  const { can } = useAuth()
  const items = [
    ...(can('user:manage') ? [{ key: 'users', label: '用户管理', children: <UsersPage /> }] : []),
    ...(can('audit:read') ? [{ key: 'audit', label: '审计日志', children: <AuditLogPage /> }] : []),
  ]
  return <Tabs items={items} />
}

const CONTENT: Record<string, () => ReactNode> = {
  personal: () => <ProfilePage />,
  processing: () => <ProcessingSettings />,
  models: () => <ModelsCard />,
  data: () => <DataSettings />,
  team: () => <TeamSettings />,
  about: () => <AboutSettings />,
}

/**
 * 设置（ADR 0063）：进来以后左边栏换成设置的几类（AppLayout 里），顶上有"返回"。
 * 这里只放右边的内容。网址是 /settings/类别，可以直接收藏某一类。
 */
export function SettingsPage() {
  const { section } = useParams()
  const sections = useSettingsSections()
  const current = sections.find((s) => s.key === section)
  if (!current) {
    return <Navigate to="/settings/processing" replace />
  }
  return (
    <div style={{ maxWidth: 1100 }}>
      {/* 个人资料页自己有标题，不重复 */}
      {current.key !== 'personal' && (
        <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 16 }}>
          {current.label}
        </Typography.Title>
      )}
      {CONTENT[current.key]()}
    </div>
  )
}
