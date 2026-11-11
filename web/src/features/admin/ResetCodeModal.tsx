import { Modal, Typography } from 'antd'
import type { ResetCode } from '../../api/types'
import { formatTime } from '../../utils/format'

/** 显示刚生成的重设码。关掉之后就再也看不到了（数据库里只有哈希）。 */
export function ResetCodeModal({ code, onClose }: { code: ResetCode | null; onClose: () => void }) {
  return (
    <Modal
      title={`${code?.username ?? ''} 的重设码`}
      open={code !== null}
      onOk={onClose}
      onCancel={onClose}
      okText="我已经告诉对方了"
      cancelButtonProps={{ style: { display: 'none' } }}
    >
      <Typography.Title
        level={2}
        copyable={{ text: code?.code }}
        style={{ textAlign: 'center', letterSpacing: 4, margin: '16px 0' }}
      >
        {code?.code}
      </Typography.Title>
      <Typography.Paragraph>
        有效到 <strong>{code ? formatTime(code.expires_at) : ''}</strong>（15 分钟），只能用一次。
      </Typography.Paragraph>
      <Typography.Paragraph>
        <strong>这不是新密码，不能直接用来登录。</strong>
        对方要在登录页点"<strong>用重设码设新密码</strong>"，输入用户名和这个码，再自己设一个新密码。
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        请当面或打电话告诉 {code?.username}，不要发到聊天群里。
        这个码关掉后就看不到了；对方没用上的话，再生成一个就行（旧的会作废）。
      </Typography.Paragraph>
    </Modal>
  )
}
