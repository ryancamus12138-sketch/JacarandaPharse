import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
  SafetyCertificateOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { Tag, Tooltip } from 'antd'
import type { ReactNode } from 'react'
import type { DocumentStatus } from '../../api/types'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const STATUS: Record<DocumentStatus, { label: string; color: string; icon: ReactNode }> = {
  uploaded: { get label() { return t('排队中') }, color: 'default', icon: <ClockCircleOutlined /> },
  processing: { get label() { return t('处理中') }, color: 'processing', icon: <SyncOutlined spin /> },
  done: { get label() { return t('待审核') }, color: 'warning', icon: <CheckCircleOutlined /> },
  failed: { get label() { return t('失败') }, color: 'error', icon: <CloseCircleOutlined /> },
  reviewed: { get label() { return t('已审核') }, color: 'success', icon: <SafetyCertificateOutlined /> },
}

/** Status tag. For failures, hovering shows the cause. */
export function StatusTag({ status, error }: { status: DocumentStatus; error?: string | null }) {
  const { t } = useTranslation()
  const { label, color, icon } = STATUS[status] ?? STATUS.uploaded
  if (!error) {
    return (
      <Tag color={color} icon={icon}>
        {label}
      </Tag>
    )
  }
  return (
    <Tooltip title={t('失败原因：{{error}}', { error })}>
      <Tag color={color} icon={icon} style={{ cursor: 'help' }}>
        {label}
        <QuestionCircleOutlined style={{ marginInlineStart: 6, fontSize: 12 }} />
      </Tag>
    </Tooltip>
  )
}
