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

const STATUS: Record<DocumentStatus, { label: string; color: string; icon: ReactNode }> = {
  uploaded: { label: '排队中', color: 'default', icon: <ClockCircleOutlined /> },
  processing: { label: '处理中', color: 'processing', icon: <SyncOutlined spin /> },
  done: { label: '待审核', color: 'warning', icon: <CheckCircleOutlined /> },
  failed: { label: '失败', color: 'error', icon: <CloseCircleOutlined /> },
  reviewed: { label: '已审核', color: 'success', icon: <SafetyCertificateOutlined /> },
}

/** 状态标签。失败时把鼠标放上去，显示失败原因。 */
export function StatusTag({ status, error }: { status: DocumentStatus; error?: string | null }) {
  const { label, color, icon } = STATUS[status] ?? STATUS.uploaded
  if (!error) {
    return (
      <Tag color={color} icon={icon}>
        {label}
      </Tag>
    )
  }
  return (
    <Tooltip title={`失败原因：${error}`}>
      <Tag color={color} icon={icon} style={{ cursor: 'help' }}>
        {label}
        <QuestionCircleOutlined style={{ marginInlineStart: 6, fontSize: 12 }} />
      </Tag>
    </Tooltip>
  )
}
