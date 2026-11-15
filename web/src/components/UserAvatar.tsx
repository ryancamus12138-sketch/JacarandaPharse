import { UserOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Avatar } from 'antd'
import { getAvatar } from '../api/auth'
import { useAuth } from '../auth/useAuth'

/**
 * Own avatar: the image if set, otherwise the first character of the name.
 *
 * The image requires the login token, so it is fetched first and turned into a blob: URL (like page images).
 * Changing the avatar changes avatar_at and thus the query key, which triggers a refetch.
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
