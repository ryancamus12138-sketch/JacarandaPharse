import { DownloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Flex,
  Popover,
  Progress,
  Row,
  Select,
  Statistic,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { type Correction, type FieldStat, type KindCount, type TemplateStat, downloadDataset, getCorrections } from '../../api/insights'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'
import { StageCell, StageSummary } from './Attribution'

const RANGES = [
  { value: 0, label: '全部' },
  { value: 7, label: '最近 7 天' },
  { value: 30, label: '最近 30 天' },
  { value: 90, label: '最近 90 天' },
]

function pct(rate: number): string {
  return `${(rate * 100).toFixed(rate > 0 && rate < 0.01 ? 1 : 0)}%`
}

function Kinds({ kinds }: { kinds: KindCount[] }) {
  if (kinds.length === 0) return <Typography.Text type="secondary">—</Typography.Text>
  return (
    <Flex gap={4} wrap>
      {kinds.map((k) => (
        <Tag key={k.kind} style={{ marginInlineEnd: 0 }}>
          {k.name} {k.count}
        </Tag>
      ))}
    </Flex>
  )
}

/** 导出审核数据：原文件、训练数据、成对测评清单。 */
function DatasetButton({ template }: { template?: string }) {
  const { message } = App.useApp()
  const [sensitive, setSensitive] = useState(false)
  const [files, setFiles] = useState(true)
  const download = useMutation({
    mutationFn: () => downloadDataset({ template, sensitive, files }),
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  const content = (
    <Flex vertical gap={8} style={{ maxWidth: 340 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        审核完成的单据打包成 zip：人审核后的值当标准答案，连同模型原来的回答、改了哪些、OCR 文字（train.jsonl），
        以及能直接用 jacara eval pairs 跑的测评清单（pairs.toml）。这是你的业务数据，不要放进代码仓库。
      </Typography.Text>
      <Checkbox checked={files} onChange={(e) => setFiles(e.target.checked)}>
        带上原文件
      </Checkbox>
      <Checkbox checked={sensitive} onChange={(e) => setSensitive(e.target.checked)}>
        连敏感字段一起导出（姓名、病历号、电话……）
      </Checkbox>
      <Button type="primary" icon={<DownloadOutlined />} loading={download.isPending} onClick={() => download.mutate()}>
        下载 zip
      </Button>
    </Flex>
  )
  return (
    <Popover content={content} title="导出审核数据" trigger="click" placement="bottomRight">
      <Button icon={<DownloadOutlined />}>导出审核数据</Button>
    </Popover>
  )
}

/**
 * 统计（ADR 0044）：审核完成的单据里，人改了多少、改的是哪种错误、错了却没标黄的有几个。
 * 按模板、按字段排，改得最多的在前面：先改进这些字段的说明、别名或规则。
 */
export function InsightsPage() {
  const { can } = useAuth()
  const [template, setTemplate] = useState<string | undefined>()
  const [days, setDays] = useState(0)
  const stats = useQuery({
    queryKey: ['insights', template, days],
    queryFn: () => getCorrections(template, days || undefined),
  })
  const data = stats.data

  const templateColumns: TableProps<TemplateStat>['columns'] = [
    { title: '模板', dataIndex: 'template_title' },
    { title: '单据', dataIndex: 'documents', width: 80, align: 'right' },
    { title: '字段', dataIndex: 'fields', width: 80, align: 'right' },
    { title: '人改了', dataIndex: 'edited', width: 80, align: 'right' },
    {
      title: '修改率',
      dataIndex: 'rate',
      width: 160,
      render: (r: number) => <Progress percent={Math.round(r * 100)} size="small" status={r > 0.1 ? 'exception' : 'normal'} />,
    },
    {
      title: <Tooltip title="人改了，但抽取时没有标黄：系统没发现的错">错了没标黄</Tooltip>,
      dataIndex: 'silent',
      width: 110,
      align: 'right',
      render: (n: number) => (n ? <Typography.Text type="danger">{n}</Typography.Text> : 0),
    },
  ]

  const fieldColumns: TableProps<FieldStat>['columns'] = [
    { title: '字段', dataIndex: 'label', render: (l: string, f) => <span>{l}<Typography.Text type="secondary" style={{ fontSize: 12 }}> · {f.template_title}</Typography.Text></span> },
    { title: '次数', dataIndex: 'total', width: 70, align: 'right' },
    { title: '人改了', dataIndex: 'edited', width: 80, align: 'right' },
    { title: '修改率', dataIndex: 'rate', width: 90, align: 'right', render: pct },
    {
      title: '错了没标黄',
      dataIndex: 'silent',
      width: 100,
      align: 'right',
      render: (n: number) => (n ? <Typography.Text type="danger">{n}</Typography.Text> : 0),
    },
    { title: '改的是哪种错', dataIndex: 'kinds', render: (k: KindCount[]) => <Kinds kinds={k} /> },
  ]

  const recentColumns: TableProps<Correction>['columns'] = [
    { title: '时间', dataIndex: 'at', width: 160, render: formatTime },
    {
      title: '单据',
      dataIndex: 'filename',
      ellipsis: true,
      render: (name: string, c) => <Link to={`/documents/${c.document_id}`}>{name}</Link>,
    },
    { title: '字段', dataIndex: 'label', width: 160, ellipsis: true },
    {
      title: '模型给的 → 人改成',
      render: (_, c) => (
        <span style={{ overflowWrap: 'anywhere' }}>
          <Typography.Text delete type="secondary">{c.before ?? '（空）'}</Typography.Text> → <Typography.Text strong>{c.after ?? '（空）'}</Typography.Text>
        </span>
      ),
    },
    { title: '哪种错', dataIndex: 'kind_name', width: 130, render: (n: string) => <Tag>{n}</Tag> },
    {
      title: '错在哪一步',
      width: 170,
      render: (_, c) => <StageCell c={c} canEdit={can('document:review')} />,
    },
    {
      title: '标黄了',
      dataIndex: 'flagged',
      width: 80,
      render: (f: boolean) => (f ? <Tag color="gold">是</Tag> : <Tag color="red">没有</Tag>),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          统计
        </Typography.Title>
        <Flex gap={8} wrap>
          <Select
            style={{ minWidth: 200 }}
            allowClear
            placeholder="全部模板"
            value={template}
            onChange={setTemplate}
            options={(data?.templates ?? []).map((t) => ({ value: t.template, label: t.template_title }))}
          />
          <Select style={{ width: 130 }} value={days} onChange={setDays} options={RANGES} />
          {can('document:export') && <DatasetButton template={template} />}
        </Flex>
      </Flex>
      {stats.isError && <Alert type="error" showIcon title="统计读不出来" description={String(stats.error)} />}
      {data && data.documents === 0 ? (
        <Card>
          <Empty description="还没有审核完成的单据。审核完成后，这里会统计人改了哪些字段、改的是哪种错。" />
        </Card>
      ) : (
        data && (
          <>
            <Row gutter={[16, 16]}>
              <Col flex="1 1 180px">
                <Card><Statistic title="审核完成的单据" value={data.documents} /></Card>
              </Col>
              <Col flex="1 1 180px">
                <Card><Statistic title="字段" value={data.fields} suffix={<Typography.Text type="secondary" style={{ fontSize: 14 }}>确认 {data.confirmed}</Typography.Text>} /></Card>
              </Col>
              <Col flex="1 1 180px">
                <Card>
                  <Statistic title="人工修改率" value={(data.rate * 100).toFixed(1)} suffix={`%（${data.edited} 个）`} />
                </Card>
              </Col>
              <Col flex="1 1 180px">
                <Card>
                  <Tooltip title="人改了，但抽取时没有标黄：系统没发现的错。越少越好">
                    <Statistic
                      title="错了没标黄"
                      value={data.silent}
                      valueStyle={data.silent ? { color: '#cf1322' } : undefined}
                    />
                  </Tooltip>
                </Card>
              </Col>
              <Col flex="1 1 180px">
                <Card>
                  <Tooltip
                    title={
                      data.timed_documents
                        ? `${data.timed_documents} 份记了用时。只算审核页开着、人在操作的时间，页面在后台或一分钟没动不算`
                        : '这次更新以后审核的单据才有用时'
                    }
                  >
                    <Statistic title="平均审核用时" value={reviewTime(data.review_seconds)} />
                  </Tooltip>
                </Card>
              </Col>
            </Row>
            <Card title="常见错误" size="small">
              <Kinds kinds={data.kinds} />
            </Card>
            <Card
              title={
                <Tooltip title="按证据判断：OCR 样本、这一轮发给模型的文字、审核时点的原文或框。证据不够的不下结论。下面最近的修改里可以点开看理由、改判">
                  <span>错在哪一步</span>
                </Tooltip>
              }
              size="small"
            >
              <StageSummary stages={data.stages ?? []} />
            </Card>
            <Card title="按模板" size="small">
              <Table rowKey="template" size="small" pagination={false} columns={templateColumns} dataSource={data.templates} />
            </Card>
            <Card title="按字段（改得最多的在前面）" size="small">
              <Table
                rowKey={(f) => `${f.template}:${f.field}`}
                size="small"
                columns={fieldColumns}
                dataSource={data.by_field}
                pagination={{ pageSize: 15, hideOnSinglePage: true }}
              />
            </Card>
            <Card title="最近的修改" size="small">
              <Table rowKey={(c) => `${c.document_id}:${c.label}:${c.at}`} size="small" pagination={false} columns={recentColumns} dataSource={data.recent} />
            </Card>
          </>
        )
      )}
    </Flex>
  )
}

/** 审核用时写成"1 分 25 秒"；没有记录的显示"—"。 */
function reviewTime(seconds: number | null): string {
  if (seconds == null) return '—'
  const total = Math.round(seconds)
  if (total < 60) return `${total} 秒`
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`
}
