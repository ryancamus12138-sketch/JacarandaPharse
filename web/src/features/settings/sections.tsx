import {
  ApiOutlined,
  DatabaseOutlined,
  InfoCircleOutlined,
  ScanOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import { useAuth } from '../../auth/useAuth'
import { useTranslation } from 'react-i18next'

export interface SettingsSection {
  key: string
  label: string
  icon: ReactNode
}

/**
 * Settings sections, shared by the sidebar (which AppLayout switches to inside settings) and the settings
 * page. The personal edition has no "Team and permissions". Only names and icons live here, without importing
 * page code, so the sidebar loads fast.
 */
export function useSettingsSections(): SettingsSection[] {
  const { t } = useTranslation()
  const { me, can } = useAuth()
  const team = me?.mode === 'team' && (can('user:manage') || can('audit:read'))
  return [
    { key: 'personal', label: t('个人'), icon: <UserOutlined /> },
    { key: 'processing', label: t('识别和处理'), icon: <ScanOutlined /> },
    { key: 'models', label: t('模型'), icon: <ApiOutlined /> },
    { key: 'data', label: t('数据和备份'), icon: <DatabaseOutlined /> },
    ...(team ? [{ key: 'team', label: t('团队和权限'), icon: <TeamOutlined /> }] : []),
    { key: 'about', label: t('关于和诊断'), icon: <InfoCircleOutlined /> },
  ]
}

const LAST_PAGE = 'jacara.lastPage'

/**
 * Page visited before entering settings, where "Back" returns. Stored in sessionStorage (this tab only);
 * falls back to the dashboard when storage is unavailable.
 */
export function rememberPage(path: string): void {
  try {
    sessionStorage.setItem(LAST_PAGE, path)
  } catch {
    // Storage unavailable: "Back" returns to the dashboard.
  }
}

export function lastPage(): string {
  try {
    return sessionStorage.getItem(LAST_PAGE) || '/dashboard'
  } catch {
    return '/dashboard'
  }
}
