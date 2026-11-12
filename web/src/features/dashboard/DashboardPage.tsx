import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FieldTimeOutlined,
  FileSearchOutlined,
} from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Flex,
  List,
  Progress,
  Row,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { getDashboard } from '../../api/documents'
import type { DocumentSummary } from '../../api/types'
import { formatTime } from '../../utils/format'
import { StatusTag } from '../documents/StatusTag'

const POLL_MS = 3000 // 有文件在排队或处理时，每 3 秒刷新一次

/** 一张统计卡。点一下去文档列表，并按 to 里的条件筛好。 */
function StatCard({
  title,
  value,
  suffix,
  icon,
  color,
  to,
  note,
}: {
  title: string
  value: number | string
  suffix?: string
  icon: ReactNode
  color: string
  to?: string
  note?: ReactNode
}) {
  const navigate = useNavigate()
  return (
    <Card
      size="small"
      hoverable={Boolean(to)}
      onClick={to ? () => void navigate(to) : undefined}
      style={{ height: '100%' }}
    >
      <Statistic
        title={
          <Flex gap={6} align="center">
            <span style={{ color }}>{icon}</span>
            {title}
          </Flex>
        }
        value={value}
        suffix={suffix}
      />
      {note && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {note}
        </Typography.Text>
      )}
    </Card>
  )
}

/**
 * 工作台（首页）：今天要做什么。
 * 上面是统计卡（点一下进入筛选好的文档列表），下面左边是"需要先处理"，右边是处理队列（带进度）。
 */
export function DashboardPage() {
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
    refetchInterval: (query) => ((query.state.data?.queue.length ?? 0) > 0 ? POLL_MS : false),
  })
  const navigate = useNavigate()

  if (dashboard.error) {
    return (
      <Alert
        type="error"
        showIcon
        title={
          dashboard.error instanceof ApiError ? dashboard.error.message : String(dashboard.error)
        }
      />
    )
  }
  const data = dashboard.data
  const c = data?.counts
  const first = data?.to_review[0]

  return (
    <Flex vertical gap={16}>
      <Row gutter={[16, 16]}>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="待审核"
            value={c?.to_review ?? '–'}
            suffix="份"
            icon={<FileSearchOutlined />}
            color="#faad14"
            to="/documents?status=done"
            note={
              c
                ? `${c.pending_fields} 个字段等你确认${c.skipped ? `，先跳过 ${c.skipped} 份` : ''}`
                : null
            }
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="排队和处理中"
            value={c ? c.queued + c.processing : '–'}
            suffix="份"
            icon={<ClockCircleOutlined />}
            color="#1677ff"
            to="/documents?status=uploaded&status=processing"
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="处理失败"
            value={c?.failed ?? '–'}
            suffix="份"
            icon={<CloseCircleOutlined />}
            color="#ff4d4f"
            to="/documents?status=failed"
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="已审核"
            value={c?.reviewed ?? '–'}
            suffix="份"
            icon={<CheckCircleOutlined />}
            color="#52c41a"
            to="/documents?status=reviewed"
            note={c ? `一共 ${c.total} 份` : null}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="今天"
            value={c?.processed_today ?? '–'}
            suffix="份处理完"
            icon={<CheckCircleOutlined />}
            color="#7b5cc4"
            note={c ? `审核完 ${c.reviewed_today} 份` : null}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title="每页用时"
            value={c?.average_page_seconds ?? '–'}
            suffix={c?.average_page_seconds != null ? '秒' : undefined}
            icon={<FieldTimeOutlined />}
            color="#7b5cc4"
            note="最近 7 天的平均"
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card
            title="需要先处理"
            extra={
              first && (
                <Button
                  type="primary"
                  icon={<ArrowRightOutlined />}
                  onClick={() => void navigate(`/documents/${first.id}`)}
                >
                  开始审核
                </Button>
              )
            }
            loading={dashboard.isPending}
          >
            {data && data.failed.length === 0 && data.to_review.length === 0 ? (
              <Empty description="没有要处理的，都审核完了" />
            ) : (
              <List<DocumentSummary>
                dataSource={[...(data?.failed ?? []), ...(data?.to_review ?? [])]}
                renderItem={(d) => (
                  <List.Item
                    actions={[
                      d.status === 'failed' ? (
                        <Link key="go" to="/documents?status=failed">
                          去重试
                        </Link>
                      ) : (
                        <Link key="go" to={`/documents/${d.id}`}>
                          审核
                        </Link>
                      ),
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Flex gap={8} align="center" wrap>
                          <Typography.Text ellipsis style={{ maxWidth: 320 }}>
                            {d.filename}
                          </Typography.Text>
                          <StatusTag status={d.status} />
                          {d.status === 'done' && d.review_count > 0 && (
                            <Tag color="gold">{d.review_count} 个字段待确认</Tag>
                          )}
                        </Flex>
                      }
                      description={
                        d.status === 'failed' ? (
                          <Typography.Text type="danger">{d.error}</Typography.Text>
                        ) : (
                          `上传于 ${formatTime(d.created_at)}`
                        )
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="处理队列" loading={dashboard.isPending}>
            {data && data.queue.length === 0 ? (
              <Empty description="没有排队的文件" />
            ) : (
              <List<DocumentSummary>
                dataSource={data?.queue ?? []}
                renderItem={(d) => (
                  <List.Item>
                    <Flex vertical gap={4} style={{ width: '100%' }}>
                      <Flex justify="space-between" gap={8}>
                        <Typography.Text ellipsis>{d.filename}</Typography.Text>
                        <StatusTag status={d.status} />
                      </Flex>
                      {d.status === 'processing' ? (
                        <>
                          <Progress percent={d.progress ?? 0} size="small" status="active" />
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {d.stage ?? '处理中'}
                          </Typography.Text>
                        </>
                      ) : (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          上传于 {formatTime(d.created_at)}，等前面的处理完
                        </Typography.Text>
                      )}
                    </Flex>
                  </List.Item>
                )}
              />
            )}
          </Card>
        </Col>
      </Row>
    </Flex>
  )
}
