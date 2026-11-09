import { Alert, Card, Descriptions, Flex, Spin, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { api, ApiError } from './api/client'
import type { Health, Me } from './api/types'

/** 这一课先做一个"连通检查"页：确认网页能通过 Vite 转发，连到后端。 */
export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // 网页一打开就执行一次：先问后端在不在，再问"我是谁"
    async function load() {
      try {
        setHealth(await api<Health>('/health'))
        setMe(await api<Me>('/auth/me'))
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)))
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  return (
    <Flex justify="center" style={{ padding: 48 }}>
      <Card title="JacarandaParse" style={{ width: 560 }}>
        {loading && <Spin />}
        {health && (
          <Alert type="success" showIcon title={`后端已连上，版本 ${health.version}`} />
        )}
        {error?.status === 401 && (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            title="团队模式：需要登录"
            description="下一课做登录页。"
          />
        )}
        {error && error.status !== 401 && (
          <Alert type="error" showIcon style={{ marginTop: 16 }} title={error.message} />
        )}
        {me && (
          <Descriptions column={1} bordered size="small" style={{ marginTop: 16 }}>
            <Descriptions.Item label="模式">
              {me.mode === 'personal' ? '个人模式（不用登录）' : '团队模式'}
            </Descriptions.Item>
            <Descriptions.Item label="角色">{me.role}</Descriptions.Item>
            <Descriptions.Item label="能做的操作">
              <Flex wrap gap={4}>
                {me.permissions.map((name) => (
                  <Tag key={name}>{name}</Tag>
                ))}
              </Flex>
            </Descriptions.Item>
          </Descriptions>
        )}
        <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          这一页只用来确认前端和后端连通了。
        </Typography.Paragraph>
      </Card>
    </Flex>
  )
}
