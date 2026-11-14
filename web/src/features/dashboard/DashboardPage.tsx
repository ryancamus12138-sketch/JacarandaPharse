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
  Tooltip,
  Typography,
  theme,
} from 'antd'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { getDashboard } from '../../api/documents'
import type { DocumentSummary } from '../../api/types'
import { formatTime } from '../../utils/format'
import { StatusTag } from '../documents/StatusTag'
import { useTranslation } from 'react-i18next'

const POLL_MS = 3000 // Refresh every 3 seconds while files are queued or processing.

/** A statistic card. Clicking it opens the document list filtered by to. */
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
  const { token } = theme.useToken()
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
            <span style={{ color, flex: 'none' }}>{icon}</span>
            {/* One line in every language, so the numbers of all cards stay aligned; the full title
                is shown on hover when it does not fit. */}
            <Typography.Text type="secondary" ellipsis={{ tooltip: title }} style={{ minWidth: 0 }}>
              {title}
            </Typography.Text>
          </Flex>
        }
        value={value}
        suffix={
          suffix && (
            // The unit is smaller than the number and secondary, so the number reads first.
            <span style={{ fontSize: token.fontSize, color: token.colorTextSecondary }}>{suffix}</span>
          )
        }
      />
      {note && (
        <Typography.Text type="secondary" ellipsis={typeof note === 'string' ? { tooltip: note } : true} style={{ fontSize: token.fontSizeSM, display: 'block' }}>
          {note}
        </Typography.Text>
      )}
    </Card>
  )
}

/**
 * Dashboard (home page): today's work.
 * Statistic cards on top (each opens a filtered document list); below, items needing attention on the left
 * and the processing queue (with progress) on the right.
 */
export function DashboardPage() {
  const { t } = useTranslation()
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
            title={t('待审核')}
            value={c?.to_review ?? '–'}
            suffix={t('份')}
            icon={<FileSearchOutlined />}
            color="#faad14"
            to="/documents?status=done"
            note={
              c
                ? c.skipped
                  ? t('{{pending_fields}} 个字段待确认，已跳过 {{skipped}} 份', { pending_fields: c.pending_fields, skipped: c.skipped })
                  : t('{{pending_fields}} 个字段待确认', { pending_fields: c.pending_fields })
                : null
            }
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title={t('排队及处理中')}
            value={c ? c.queued + c.processing : '–'}
            suffix={t('份')}
            icon={<ClockCircleOutlined />}
            color="#1677ff"
            to="/documents?status=uploaded&status=processing"
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title={t('处理失败')}
            value={c?.failed ?? '–'}
            suffix={t('份')}
            icon={<CloseCircleOutlined />}
            color="#ff4d4f"
            to="/documents?status=failed"
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title={t('已审核')}
            value={c?.reviewed ?? '–'}
            suffix={t('份')}
            icon={<CheckCircleOutlined />}
            color="#52c41a"
            to="/documents?status=reviewed"
            note={c ? t('共 {{total}} 份', { total: c.total }) : null}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title={t('今天')}
            value={c?.processed_today ?? '–'}
            suffix={t('份已处理')}
            icon={<CheckCircleOutlined />}
            color="#7b5cc4"
            note={c ? t('已审核 {{reviewed_today}} 份', { reviewed_today: c.reviewed_today }) : null}
          />
        </Col>
        <Col xs={12} md={8} xl={4}>
          <StatCard
            title={t('每页耗时')}
            value={c?.average_page_seconds ?? '–'}
            suffix={c?.average_page_seconds != null ? t('秒') : undefined}
            icon={<FieldTimeOutlined />}
            color="#7b5cc4"
            note={
              c?.stage_seconds ? (
                <Tooltip title={t('每份单据平均耗时：OCR 识别、识别后排队等待模型、模型抽取及核对。OCR 与模型两个阶段并行运行，批量导入时二者耗时相互重叠')}>
                  {t('每份：OCR {{read}} 秒 · 模型 {{extract}} 秒', { read: c.stage_seconds.read ?? '–', extract: c.stage_seconds.extract ?? '–' })}
                </Tooltip>
              ) : (
                t('最近 7 天平均值')
              )
            }
          />
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card
            title={t('待处理事项')}
            extra={
              first && (
                <Button
                  type="primary"
                  icon={<ArrowRightOutlined />}
                  onClick={() => void navigate(`/documents/${first.id}`)}
                >
                  {t('开始审核')}
                </Button>
              )
            }
            loading={dashboard.isPending}
          >
            {data && data.failed.length === 0 && data.to_review.length === 0 ? (
              <Empty description={t('没有待处理事项，全部审核完毕')} />
            ) : (
              <List<DocumentSummary>
                dataSource={[...(data?.failed ?? []), ...(data?.to_review ?? [])]}
                renderItem={(d) => (
                  <List.Item
                    actions={[
                      d.status === 'failed' ? (
                        <Link key="go" to="/documents?status=failed">
                          {t('重试')}
                        </Link>
                      ) : (
                        <Link key="go" to={`/documents/${d.id}`}>
                          {t('审核')}
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
                            <Tag color="gold">{t('{{review_count}} 个字段待确认', { review_count: d.review_count })}</Tag>
                          )}
                        </Flex>
                      }
                      description={
                        d.status === 'failed' ? (
                          <Typography.Text type="danger">{d.error}</Typography.Text>
                        ) : (
                          t('上传于 {{formatTime}}', { formatTime: formatTime(d.created_at) })
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
          <Card title={t('处理队列')} loading={dashboard.isPending}>
            {data && data.queue.length === 0 ? (
              <Empty description={t('队列中没有文件')} />
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
                            {d.stage ?? t('处理中')}
                          </Typography.Text>
                        </>
                      ) : (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {t('上传于 {{formatTime}}，等待前序文件处理完成', { formatTime: formatTime(d.created_at) })}
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
