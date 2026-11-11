import { Input, Modal, Typography } from 'antd'
import { useState } from 'react'

export interface ReasonRequest {
  title: string
  /** 说明这一步会做什么。 */
  description: string
  placeholder: string
  okText: string
  danger?: boolean
  onOk: (reason: string) => void
}

/** 要写原因的操作（清空、删除一行、恢复一行）弹出这个框。原因必填，以后查记录时能看懂。 */
export function ReasonDialog({
  request,
  onClose,
}: {
  request: ReasonRequest | null
  onClose: () => void
}) {
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
      cancelText="取消"
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
