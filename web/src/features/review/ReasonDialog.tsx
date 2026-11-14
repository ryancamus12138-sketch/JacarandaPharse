import { Input, Modal, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export interface ReasonRequest {
  title: string
  /** Explains what this step will do. */
  description: string
  placeholder: string
  okText: string
  danger?: boolean
  onOk: (reason: string) => void
}

/**
 * Dialog for operations that require a reason (clear, delete a row, restore a row). The reason is mandatory
 * so the history remains understandable.
 */
export function ReasonDialog({
  request,
  onClose,
}: {
  request: ReasonRequest | null
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')
  const ok = () => {
    if (!request || !reason.trim()) {
      return
    }
    request.onOk(reason.trim())
    setReason('')
    onClose()
  }
  return (
    <Modal
      open={request !== null}
      title={request?.title}
      okText={request?.okText}
      okButtonProps={{ danger: request?.danger, disabled: !reason.trim() }}
      cancelText={t('取消')}
      onOk={ok}
      onCancel={() => {
        setReason('')
        onClose()
      }}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary">{request?.description}</Typography.Paragraph>
      <Input
        autoFocus
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        onPressEnter={ok}
        placeholder={request?.placeholder}
        maxLength={500}
      />
    </Modal>
  )
}
