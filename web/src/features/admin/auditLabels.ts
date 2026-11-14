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
  'document:process': '重新处理（重试）',
  'document:reprocess': '换模板重新处理',
  'document:skip': '先跳过',
  'document:unskip': '取消跳过',
  'comment:create': '写备注',
  'comment:resolve': '备注标为已解决',
  'comment:reopen': '重新打开备注',
  'comment:delete': '删除备注',
  'profile:update': '改昵称',
  'profile:avatar': '换头像',
  'profile:avatar_remove': '去掉头像',
  'document:adopt_previous': '沿用上一轮的审核',
  'document:review': '完成审核',
  'document:export': '导出',
  'document:delete': '删除文档',
  'export:download': '重新下载导出文件',
  'value:edit': '修改字段',
  'value:confirm': '确认字段',
  'value:confirm_many': '批量确认字段',
  'value:compute': '按公式重算',
  'template:create': '新建模板',
  'template:save': '保存模板新版本',
  'template:archive': '归档模板',
  'template:restore': '恢复模板',
  'template:delete': '删除模板',
  'user:create': '新建用户',
  'user:update': '修改用户',
  'user:reset_code': '生成重设码',
  'user:password': '命令行重设密码',
  'audit:export': '导出审计日志',
  'ocr_samples:settings': 'OCR 样本：改收集开关',
  'ocr_samples:update': 'OCR 样本：改原文、分类或撤回',
  'ocr_samples:export': 'OCR 样本：导出',
  'ocr_samples:split': 'OCR 样本：改分组的划分',
  'ocr_samples:pair': 'OCR 样本：确认是不是同一张单据',
  'ocr_samples:detach': 'OCR 样本：拆开分组',
  'ocr_samples:backfill': 'OCR 样本：从以前的修改补收',
  'ocr_samples:verify': 'OCR 样本：复核通过',
  'ocr_samples:confirmed': 'OCR 样本：收认对的行',
  'ocr_samples:det_save': '检测样本：保存框',
  'ocr_samples:det_confirm': '检测样本：确认一页',
  'ocr_samples:det_exclude': '检测样本：不用这一页',
  'ocr_samples:det_export': '检测样本：导出',
  'hub:model_import': '模型库：导入新版本',
  'hub:model_use': '模型库：用这个版本',
  'hub:model_card': '模型库：改模型卡',
  'hub:model_delete': '模型库：删除版本',
  'hub:model_export': '模型库：打包下载',
  'hub:dataset_import': '数据集库：上传',
  'hub:dataset_save': '数据集库：从系统里存进来',
  'hub:dataset_download': '数据集库：下载',
  'hub:dataset_delete': '数据集库：删除',
  'hub:eval': 'OCR 模型评测',
  'attribution:override': '统计：改判错在哪一步',
}

/** 筛选下拉的选项：按开头筛（后端 ?action=document: 这样）。 */
export const ACTION_FILTERS = [
  { value: '', label: '全部操作' },
  { value: 'auth:', label: '登录、退出、改密码' },
  { value: 'document:', label: '文档：上传、处理、审核、导出' },
  { value: 'value:', label: '字段：修改、确认' },
  { value: 'template:', label: '模板：新建、保存、归档、删除' },
  { value: 'user:', label: '用户管理' },
  { value: 'ocr_samples:', label: 'OCR 样本、检测样本' },
  { value: 'hub:', label: '模型和数据集' },
  { value: 'audit:', label: '导出审计日志' },
  { value: 'attribution:', label: '统计：改判错在哪一步' },
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
/** 错误归因的阶段（ADR 0053）。 */
const STAGE_NAMES: Record<string, string> = {
  ocr_misread: 'OCR 认错了字',
  ocr_uncovered: 'OCR 没认出这块',
  extraction: '模型取错或漏取',
  matching: '标准化或出处匹配',
  template: '模板或规则',
  mixed: '几处都有问题',
  unknown: '证据不够',
}

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
    case 'value:compute':
      return `参与计算的值改了：${Array.isArray(d.fields) ? d.fields.map((f) => fieldLabel(String(f))).join('、') : ''}`
    case 'auth:login_failed':
    case 'auth:reset_failed':
      return `用户名：${String(d.username)}`
    case 'attribution:override': {
      const name = (s: unknown) => (s ? (STAGE_NAMES[String(s)] ?? String(s)) : '')
      const change = d.after ? `改判成「${name(d.after)}」` : '撤销改判'
      return `${change}（自动判断：${name(d.auto)}）${d.note ? ` · ${String(d.note)}` : ''}`
    }
    case 'ocr_samples:backfill':
      return `补收了 ${String(d.created)} 条，待复核`
    case 'audit:export':
      return `导出了 ${String(d.count)} 条`
    default:
      return Object.entries(d)
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`)
        .join(' · ')
  }
}
