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

export interface SettingsSection {
  key: string
  label: string
  icon: ReactNode
}

/**
 * 设置里有哪几类（ADR 0063）。左边栏（AppLayout 进了设置后换成这个）和设置页共用同一份。
 * 个人版没有"团队和权限"。这里只放名字和图标，不引入各页的代码，左边栏加载快。
 */
export function useSettingsSections(): SettingsSection[] {
  const { me, can } = useAuth()
  const team = me?.mode === 'team' && (can('user:manage') || can('audit:read'))
  return [
    { key: 'personal', label: '个人', icon: <UserOutlined /> },
    { key: 'processing', label: '识别和处理', icon: <ScanOutlined /> },
    { key: 'models', label: '模型', icon: <ApiOutlined /> },
    { key: 'data', label: '数据和备份', icon: <DatabaseOutlined /> },
    ...(team ? [{ key: 'team', label: '团队和权限', icon: <TeamOutlined /> }] : []),
    { key: 'about', label: '关于和诊断', icon: <InfoCircleOutlined /> },
  ]
}

const LAST_PAGE = 'jacara.lastPage'

/** 进设置之前在哪一页："返回"回到那里。存在 sessionStorage（只在这个标签页里），存不了就回工作台。 */
export function rememberPage(path: string): void {
  try {
    sessionStorage.setItem(LAST_PAGE, path)
  } catch {
    // 浏览器不让存：返回时回工作台
  }
}

export function lastPage(): string {
  try {
    return sessionStorage.getItem(LAST_PAGE) || '/dashboard'
  } catch {
    return '/dashboard'
  }
}
