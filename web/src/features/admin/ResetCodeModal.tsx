import { Modal, Typography } from 'antd'
import type { ResetCode } from '../../api/types'
import { formatTime } from '../../utils/format'
import { useTranslation } from 'react-i18next'

/**
 * Shows the newly generated reset code. Once closed it can never be shown again (the database stores only its
 * hash).
 */
export function ResetCodeModal({ code, onClose }: { code: ResetCode | null; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Modal
      title={t('{{value}} 的重设码', { value: code?.username ?? '' })}
      open={code !== null}
      onOk={onClose}
      onCancel={onClose}
      okText={t('我已经告诉对方了')}
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
        {t('有效到')}{' '}<strong>{code ? formatTime(code.expires_at) : ''}</strong>{t('（15 分钟），只能用一次。')}
      </Typography.Paragraph>
      <Typography.Paragraph>
        <strong>{t('这不是新密码，不能直接用来登录。')}</strong>
        {t('对方要在登录页点"')}<strong>{t('用重设码设新密码')}</strong>{t('"，输入用户名和这个码，再自己设一个新密码。')}
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        {t('请当面或打电话告诉 {{username}}，不要发到聊天群里。 这个码关掉后就看不到了；对方没用上的话，再生成一个就行（旧的会作废）。', { username: code?.username })}
      </Typography.Paragraph>
    </Modal>
  )
}
