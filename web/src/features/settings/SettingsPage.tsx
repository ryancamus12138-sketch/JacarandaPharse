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
import { useTranslation } from 'react-i18next'

function TeamSettings() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const items = [
    ...(can('user:manage') ? [{ key: 'users', label: t('用户管理'), children: <UsersPage /> }] : []),
    ...(can('audit:read') ? [{ key: 'audit', label: t('审计日志'), children: <AuditLogPage /> }] : []),
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
 * Settings: on entry the sidebar switches to the settings sections (in AppLayout) with "Back" on top. This
 * component renders only the content on the right. URLs are /settings/SECTION, so each section can be
 * bookmarked.
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
      {/* The profile page has its own title; avoid duplicating it. */}
      {current.key !== 'personal' && (
        <Typography.Title level={3} style={{ marginTop: 0, marginBottom: 16 }}>
          {current.label}
        </Typography.Title>
      )}
      {CONTENT[current.key]()}
    </div>
  )
}
