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
      okText={t('已告知用户')}
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
        {t('有效期至')}{' '}<strong>{code ? formatTime(code.expires_at) : ''}</strong>{t('（15 分钟），仅可使用一次。')}
      </Typography.Paragraph>
      <Typography.Paragraph>
        <strong>{t('此码不是新密码，不能直接用于登录。')}</strong>
        {t('用户需在登录页点击“')}<strong>{t('使用重设码设置新密码')}</strong>{t('”，输入用户名和此码，然后设置新密码。')}
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        {t('请当面或通过电话告知 {{username}}，请勿在群聊中发送。关闭此窗口后将无法再次查看；如用户未能使用，可重新生成（旧码将失效）。', { username: code?.username })}
      </Typography.Paragraph>
    </Modal>
  )
}
