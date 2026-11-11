import { useQuery } from '@tanstack/react-query'
import { Spin, Timeline, Typography } from 'antd'
import type { Revision } from '../../api/types'
import { getValueHistory } from '../../api/values'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'

const COLORS: Record<Revision['action'], string> = {
  edit: 'blue',
  confirm: 'green',
  delete: 'red',
  restore: 'gray',
}

function describe(r: Revision): string {
  switch (r.action) {
    case 'confirm':
      return ' 确认值是对的'
    case 'delete':
      return ' 删除了这一行'
    case 'restore':
      return ' 恢复了这一行'
    default:
      return r.evidence
        ? ` 从原文选取「${r.evidence.row_text}」→ ${r.new_value ?? '（空）'}`
        : ` 改成 ${r.new_value ?? '（原文没有）'}`
  }
}

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
    <div style={{ margin: '12px 0 0 0' }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        修改历史
      </Typography.Text>
      <Timeline
        style={{ marginTop: 8, fontSize: 12 }}
        items={[
          {
            color: 'gray',
            content:
              history.data.value.origin === 'manual'
                ? '审核时人工增加'
                : `模型抽出：${original ?? '（原文没有）'}`,
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
