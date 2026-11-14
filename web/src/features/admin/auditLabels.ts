import type { AuditLog } from '../../api/types'
import { fieldLabel } from '../review/fields'
import { t } from '../../i18n'

/** Display names of audit log actions. */
export const ACTION_LABELS: Record<string, string> = {
  get 'auth:login'() { return t('登录') },
  get 'auth:login_failed'() { return t('登录失败') },
  get 'auth:logout'() { return t('退出') },
  get 'auth:password_change'() { return t('修改自己的密码') },
  get 'auth:password_reset'() { return t('使用重设码设置新密码') },
  get 'auth:reset_failed'() { return t('重设码错误') },
  get 'document:upload'() { return t('上传') },
  get 'document:process'() { return t('重新处理（重试）') },
  get 'document:reprocess'() { return t('更换模板并重新处理') },
  get 'document:skip'() { return t('跳过') },
  get 'document:unskip'() { return t('取消跳过') },
  get 'comment:create'() { return t('添加备注') },
  get 'comment:resolve'() { return t('备注标为已解决') },
  get 'comment:reopen'() { return t('重新打开备注') },
  get 'comment:delete'() { return t('删除备注') },
  get 'profile:update'() { return t('修改昵称') },
  get 'profile:avatar'() { return t('更换头像') },
  get 'profile:avatar_remove'() { return t('移除头像') },
  get 'document:adopt_previous'() { return t('沿用上一轮的审核') },
  get 'document:review'() { return t('完成审核') },
  get 'document:export'() { return t('导出') },
  get 'document:delete'() { return t('删除文档') },
  get 'export:download'() { return t('重新下载导出文件') },
  get 'value:edit'() { return t('修改字段') },
  get 'value:confirm'() { return t('确认字段') },
  get 'value:confirm_many'() { return t('批量确认字段') },
  get 'value:compute'() { return t('按公式重算') },
  get 'template:create'() { return t('新建模板') },
  get 'template:save'() { return t('保存模板新版本') },
  get 'template:archive'() { return t('归档模板') },
  get 'template:restore'() { return t('恢复模板') },
  get 'template:delete'() { return t('删除模板') },
  get 'user:create'() { return t('新建用户') },
  get 'user:update'() { return t('修改用户') },
  get 'user:reset_code'() { return t('生成重设码') },
  get 'user:password'() { return t('命令行重设密码') },
  get 'audit:export'() { return t('导出审计日志') },
  get 'ocr_samples:settings'() { return t('OCR 样本：修改收集开关') },
  get 'ocr_samples:update'() { return t('OCR 样本：修改原文、分类或撤回') },
  get 'ocr_samples:export'() { return t('OCR 样本：导出') },
  get 'ocr_samples:split'() { return t('OCR 样本：修改分组的划分') },
  get 'ocr_samples:pair'() { return t('OCR 样本：确认是否为同一张单据') },
  get 'ocr_samples:detach'() { return t('OCR 样本：拆分分组') },
  get 'ocr_samples:backfill'() { return t('OCR 样本：从历史修改中补收') },
  get 'ocr_samples:verify'() { return t('OCR 样本：复核通过') },
  get 'ocr_samples:confirmed'() { return t('OCR 样本：收集识别正确的行') },
  get 'ocr_samples:det_save'() { return t('检测样本：保存文本框') },
  get 'ocr_samples:det_confirm'() { return t('检测样本：确认页面') },
  get 'ocr_samples:det_exclude'() { return t('检测样本：排除页面') },
  get 'ocr_samples:det_export'() { return t('检测样本：导出') },
  get 'hub:model_import'() { return t('模型库：导入新版本') },
  get 'hub:model_use'() { return t('模型库：启用版本') },
  get 'hub:model_card'() { return t('模型库：编辑模型卡') },
  get 'hub:model_delete'() { return t('模型库：删除版本') },
  get 'hub:model_export'() { return t('模型库：导出下载') },
  get 'hub:dataset_import'() { return t('数据集库：上传') },
  get 'hub:dataset_save'() { return t('数据集库：从系统保存') },
  get 'hub:dataset_download'() { return t('数据集库：下载') },
  get 'hub:dataset_delete'() { return t('数据集库：删除') },
  get 'hub:eval'() { return t('OCR 模型评测') },
  get 'attribution:override'() { return t('统计：修改错误归因') },
}

/** Filter options: prefix filters (backend ?action=document:). */
export const ACTION_FILTERS = [
  { value: '', get label() { return t('全部操作') } },
  { value: 'auth:', get label() { return t('登录、退出、修改密码') } },
  { value: 'document:', get label() { return t('文档：上传、处理、审核、导出') } },
  { value: 'value:', get label() { return t('字段：修改、确认') } },
  { value: 'template:', get label() { return t('模板：新建、保存、归档、删除') } },
  { value: 'user:', get label() { return t('用户管理') } },
  { value: 'ocr_samples:', get label() { return t('OCR 样本、检测样本') } },
  { value: 'hub:', get label() { return t('模型和数据集') } },
  { value: 'audit:', get label() { return t('导出审计日志') } },
  { value: 'attribution:', get label() { return t('统计：修改错误归因') } },
]

const ROLE_LABELS: Record<string, string> = { get admin() { return t('管理员') }, get member() { return t('使用者') } }

/** User changes in plain language: role, granted permissions, active state, name. */
function describeUser(d: Record<string, unknown>, permission: (name: string) => string): string {
  const parts: string[] = []
  if (typeof d.role === 'string') {
    parts.push(t('角色：{{value}}', { value: ROLE_LABELS[d.role] ?? d.role }))
  }
  if (Array.isArray(d.permissions)) {
    const names = d.permissions.map((p) => permission(String(p)))
    parts.push(t('开通：{{names}}', { names: names.length > 0 ? names.join(t('、')) : t('（无）') }))
  }
  if (typeof d.is_active === 'boolean') {
    parts.push(d.is_active ? t('启用') : t('停用'))
  }
  if (typeof d.display_name === 'string') {
    parts.push(t('昵称修改为：{{display_name}}', { display_name: d.display_name }))
  }
  return parts.join(' · ')
}

/**
 * Render detail as a sentence; unhandled keys are listed as is.
 * permission: permission name → display name (from /users/permissions).
 */
/** Error attribution stages. */
const STAGE_NAMES: Record<string, string> = {
  get ocr_misread() { return t('OCR 识别错误') },
  get ocr_uncovered() { return t('OCR 漏识别') },
  get extraction() { return t('模型抽取错误或遗漏') },
  get matching() { return t('标准化或出处匹配问题') },
  get template() { return t('模板或规则') },
  get mixed() { return t('多个环节有问题') },
  get unknown() { return t('证据不足') },
}

export function describe(log: AuditLog, permission: (name: string) => string): string {
  const d = log.detail
  switch (log.action) {
    case 'user:create':
    case 'user:update':
      return describeUser(d, permission)
    case 'document:upload':
      return t('文件：{{value}}', { value: String(d.filename) })
    case 'document:export': {
      const count = Array.isArray(d.documents) ? d.documents.length : 0
      return t('{{count}} 个文档 · {{format}}', { count, format: d.format === 'zip' ? t('zip（每份一个 Excel）') : 'Excel' })
    }
    case 'value:edit':
      return t('{{field}} 修改为 {{value}}', { field: fieldLabel(String(d.field)), value: d.value === null ? t('（原文缺失）') : String(d.value) })
    case 'value:confirm':
      return t('{{field}}：{{value}}', { field: fieldLabel(String(d.field)), value: d.value === null ? t('（原文缺失）') : String(d.value) })
    case 'value:compute':
      return t('参与计算的值已修改：{{fields}}', { fields: Array.isArray(d.fields) ? d.fields.map((f) => fieldLabel(String(f))).join(t('、')) : '' })
    case 'auth:login_failed':
    case 'auth:reset_failed':
      return t('用户名：{{value}}', { value: String(d.username) })
    case 'attribution:override': {
      const name = (s: unknown) => (s ? (STAGE_NAMES[String(s)] ?? String(s)) : '')
      const change = d.after ? t('改判为「{{name}}」', { name: name(d.after) }) : t('撤销改判')
      return t('{{change}}（自动判断：{{name}}）{{value}}', { change, name: name(d.auto), value: d.note ? ` · ${String(d.note)}` : '' })
    }
    case 'ocr_samples:backfill':
      return t('补收 {{value}} 条，待复核', { value: String(d.created) })
    case 'audit:export':
      return t('导出 {{value}} 条', { value: String(d.count) })
    default:
      return Object.entries(d)
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : String(value)}`)
        .join(' · ')
  }
}
