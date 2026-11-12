import { UserOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Avatar } from 'antd'
import { getAvatar } from '../api/auth'
import { useAuth } from '../auth/useAuth'

/**
 * 自己的头像。有头像就显示图片，没有就显示名字的第一个字。
 *
 * 图片要带登录 token 才能取，所以先用 fetch 取回来再变成 blob: 地址（和单据图片一样）。
 * 换了头像 avatar_at 会变，查询的 key 跟着变，就会重新取。
 */
export function UserAvatar({ size = 32 }: { size?: number }) {
  const { me } = useAuth()
  const version = me?.avatar_at ?? null
  const picture = useQuery({
    queryKey: ['avatar', version],
    queryFn: async () => URL.createObjectURL(await getAvatar()),
    enabled: version !== null,
    staleTime: Infinity,
  })
  const name = me?.shown_name || me?.user?.display_name || me?.user?.username || ''
  const first = Array.from(name.trim())[0]
  if (version !== null && picture.data) {
    return <Avatar size={size} src={picture.data} alt={name} />
  }
  return (
    <Avatar size={size} style={{ background: '#7c5cbf', flexShrink: 0 }} icon={first ? undefined : <UserOutlined />}>
      {first}
    </Avatar>
  )
}
