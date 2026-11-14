import { useQuery } from '@tanstack/react-query'
import { Spin, Timeline, Typography } from 'antd'
import type { Revision } from '../../api/types'
import { getValueHistory } from '../../api/values'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const COLORS: Record<Revision['action'], string> = {
  edit: 'blue',
  confirm: 'green',
  delete: 'red',
  restore: 'gray',
  compute: 'cyan',
}

function describe(r: Revision): string {
  switch (r.action) {
    case 'confirm':
      return t(' 确认值是对的')
    case 'delete':
      return t(' 删除了这一行')
    case 'restore':
      return t(' 恢复了这一行')
    case 'compute':
      return ' ' + t('改了参与计算的值，按公式重算 → {{value}}', { value: r.new_value ?? t('（算不出来）') })
    default:
      return r.evidence
        ? ' ' + t('从原文选取「{{text}}」→ {{value}}', { text: r.evidence.row_text, value: r.new_value ?? t('（空）') })
        : ' ' + t('改成 {{value}}', { value: r.new_value ?? t('（原文没有）') })
  }
}

/** History of a field: what the model extracted, then who changed it to what, when and why. */
export function RevisionHistory({ valueId }: { valueId: string }) {
  const { t } = useTranslation()
  const { me } = useAuth()
  const history = useQuery({
    queryKey: ['value-history', valueId],
    queryFn: () => getValueHistory(valueId),
  })

  if (history.isPending) {
    return <Spin size="small" />
  }
  if (!history.data) {
    return null
  }

  // Personal mode has a single user; team mode identifies one's own entries by user_id.
  const who = (userId: string) =>
    me?.mode === 'personal' || me?.user?.id === userId ? t('你') : t('其他成员')
  const original = history.data.value.value

  return (
    <div style={{ margin: '12px 0 0 0' }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('修改历史')}
      </Typography.Text>
      <Timeline
        style={{ marginTop: 8, fontSize: 12 }}
        items={[
          {
            color: 'gray',
            content:
              history.data.value.origin === 'manual'
                ? t('审核时人工增加')
                : history.data.value.origin === 'computed'
                  ? t('按公式算出：{{value}}', { value: original ?? t('（算不出来）') })
                  : t('模型抽出：{{value}}', { value: original ?? t('（原文没有）') }),
          },
          ...history.data.revisions.map((r) => ({
            key: r.id,
            color: COLORS[r.action],
            content: (
              <>
                <div>
                  #{r.seq} {who(r.user_id)}
                  {describe(r)}
                </div>
                {r.reason && <Typography.Text type="secondary">{t('原因：{{reason}}', { reason: r.reason })}</Typography.Text>}
                <div>
                  <Typography.Text type="secondary">{formatTime(r.created_at)}</Typography.Text>
                </div>
              </>
            ),
          })),
        ]}
      />
    </div>
  )
}
