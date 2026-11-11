import type { AuditLog } from '../../api/types'
import { fieldLabel } from '../review/fields'

/** 审计日志里操作的中文名。 */
export const ACTION_LABELS: Record<string, string> = {
  'auth:login': '登录',
  'auth:login_failed': '登录失败',
  'auth:logout': '退出',
  'auth:password_change': '修改自己的密码',
  'auth:password_reset': '用重设码设新密码',
  'auth:reset_failed': '重设码不对',
  'document:upload': '上传',
  'document:process': '重新处理',
  'document:review': '完成审核',
  'document:export': '导出',
  'value:edit': '修改字段',
  'value:confirm': '确认字段',
  'user:create': '新建用户',
  'user:update': '修改用户',
  'user:reset_code': '生成重设码',
  'user:password': '命令行重设密码',
}

/** 筛选下拉的选项：按开头筛（后端 ?action=document: 这样）。 */
export const ACTION_FILTERS = [
  { value: '', label: '全部操作' },
  { value: 'auth:', label: '登录、退出、改密码' },
  { value: 'document:', label: '文档：上传、处理、审核、导出' },
  { value: 'value:', label: '字段：修改、确认' },
  { value: 'user:', label: '用户管理' },
]

const ROLE_LABELS: Record<string, string> = { admin: '管理员', member: '使用者' }

/** 用户的改动变成人话：角色、开通的权限、启用/停用、名字。 */
function describeUser(d: Record<string, unknown>, permission: (name: string) => string): string {
  const parts: string[] = []
  if (typeof d.role === 'string') {
    parts.push(`角色：${ROLE_LABELS[d.role] ?? d.role}`)
  }
  if (Array.isArray(d.permissions)) {
    const names = d.permissions.map((p) => permission(String(p)))
    parts.push(`开通：${names.length > 0 ? names.join('、') : '（无）'}`)
  }
  if (typeof d.is_active === 'boolean') {
    parts.push(d.is_active ? '启用' : '停用')
  }
  if (typeof d.display_name === 'string') {
    parts.push(`名字改成：${d.display_name}`)
  }
  return parts.join(' · ')
}

/**
 * 把 detail 变成一句人话。没特别处理的就原样列出来。
 * permission：权限名 → 中文（来自 /users/permissions）。
 */
export function describe(log: AuditLog, permission: (name: string) => string): string {
  const d = log.detail
  switch (log.action) {
    case 'user:create':
    case 'user:update':
      return describeUser(d, permission)
    case 'document:upload':
      return `文件：${String(d.filename)}`
    case 'document:export': {
      const count = Array.isArray(d.documents) ? d.documents.length : 0
      return `${count} 个文档 · ${d.format === 'zip' ? 'zip（每份一个 Excel）' : 'Excel'}`
    }
    case 'value:edit':
      return `${fieldLabel(String(d.field))} 改成 ${d.value === null ? '（原文没有）' : String(d.value)}`
    case 'value:confirm':
      return `${fieldLabel(String(d.field))}：${d.value === null ? '（原文没有）' : String(d.value)}`
    case 'auth:login_failed':
    case 'auth:reset_failed':
      return `用户名：${String(d.username)}`
    default:
      return Object.entries(d)
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`)
        .join(' · ')
  }
}
