import { useQuery } from '@tanstack/react-query'
import { Spin, Timeline, Typography } from 'antd'
import { getValueHistory } from '../../api/values'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'

/** 一个字段的经历：模型抽出了什么，之后谁、什么时候、改成了什么、为什么。 */
export function RevisionHistory({ valueId }: { valueId: string }) {
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

  // 个人模式只有一个人；团队模式按 user_id 认出是不是自己
  const who = (userId: string) =>
    me?.mode === 'personal' || me?.user?.id === userId ? '你' : '其他成员'
  const original = history.data.value.value

  return (
    <div style={{ margin: '12px 0 0 88px' }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        修改历史
      </Typography.Text>
      <Timeline
        style={{ marginTop: 8, fontSize: 12 }}
        items={[
          {
            color: 'gray',
            content: `模型抽出：${original ?? '（原文没有）'}`,
          },
          ...history.data.revisions.map((r) => ({
            key: r.id,
            color: r.action === 'confirm' ? 'green' : 'blue',
            content: (
              <>
                <div>
                  #{r.seq} {who(r.user_id)}
                  {r.action === 'confirm' ? ' 确认值是对的' : ` 改成 ${r.new_value ?? '（原文没有）'}`}
                </div>
                {r.reason && <Typography.Text type="secondary">原因：{r.reason}</Typography.Text>}
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
