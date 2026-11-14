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
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const RANGES = [
  { value: 0, get label() { return t('全部') } },
  { value: 7, get label() { return t('最近 7 天') } },
  { value: 30, get label() { return t('最近 30 天') } },
  { value: 90, get label() { return t('最近 90 天') } },
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

/** Export review data: original files, training data, paired evaluation manifest. */
function DatasetButton({ template }: { template?: string }) {
  const { t } = useTranslation()
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
        {t('将已完成审核的单据打包为 zip：以人工审核后的值作为标准答案，并附模型原始输出、修改内容及 OCR 文字（train.jsonl），以及可直接用于 jacara eval pairs 的测评清单（pairs.toml）。导出内容属于业务数据，请勿提交到代码仓库。')}
      </Typography.Text>
      <Checkbox checked={files} onChange={(e) => setFiles(e.target.checked)}>
        {t('包含原文件')}
      </Checkbox>
      <Checkbox checked={sensitive} onChange={(e) => setSensitive(e.target.checked)}>
        {t('包含敏感字段（姓名、病历号、电话等）')}
      </Checkbox>
      <Button type="primary" icon={<DownloadOutlined />} loading={download.isPending} onClick={() => download.mutate()}>
        {t('下载 zip')}
      </Button>
    </Flex>
  )
  return (
    <Popover content={content} title={t('导出审核数据')} trigger="click" placement="bottomRight">
      <Button icon={<DownloadOutlined />}>{t('导出审核数据')}</Button>
    </Popover>
  )
}

/**
 * Statistics: for reviewed documents, how often values were corrected, the error kinds, and how many wrong
 * values were not flagged.
 * Sorted by template and field, most corrected first: improve those fields' descriptions, aliases or rules
 * first.
 */
export function InsightsPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [template, setTemplate] = useState<string | undefined>()
  const [days, setDays] = useState(0)
  const stats = useQuery({
    queryKey: ['insights', template, days],
    queryFn: () => getCorrections(template, days || undefined),
  })
  const data = stats.data

  const templateColumns: TableProps<TemplateStat>['columns'] = [
    { title: t('模板'), dataIndex: 'template_title' },
    { title: t('单据'), dataIndex: 'documents', width: 80, align: 'right' },
    { title: t('字段'), dataIndex: 'fields', width: 80, align: 'right' },
    { title: t('人工修改'), dataIndex: 'edited', width: 80, align: 'right' },
    {
      title: t('修改率'),
      dataIndex: 'rate',
      width: 160,
      render: (r: number) => <Progress percent={Math.round(r * 100)} size="small" status={r > 0.1 ? 'exception' : 'normal'} />,
    },
    {
      title: <Tooltip title={t('经人工修改但抽取时未标黄：系统未检出的错误')}>{t('错误未标黄')}</Tooltip>,
      dataIndex: 'silent',
      width: 110,
      align: 'right',
      render: (n: number) => (n ? <Typography.Text type="danger">{n}</Typography.Text> : 0),
    },
  ]

  const fieldColumns: TableProps<FieldStat>['columns'] = [
    { title: t('字段'), dataIndex: 'label', render: (l: string, f) => <span>{l}<Typography.Text type="secondary" style={{ fontSize: 12 }}> · {f.template_title}</Typography.Text></span> },
    { title: t('次数'), dataIndex: 'total', width: 70, align: 'right' },
    { title: t('人工修改'), dataIndex: 'edited', width: 80, align: 'right' },
    { title: t('修改率'), dataIndex: 'rate', width: 90, align: 'right', render: pct },
    {
      title: t('错误未标黄'),
      dataIndex: 'silent',
      width: 100,
      align: 'right',
      render: (n: number) => (n ? <Typography.Text type="danger">{n}</Typography.Text> : 0),
    },
    { title: t('错误类型'), dataIndex: 'kinds', render: (k: KindCount[]) => <Kinds kinds={k} /> },
  ]

  const recentColumns: TableProps<Correction>['columns'] = [
    { title: t('时间'), dataIndex: 'at', width: 160, render: formatTime },
    {
      title: t('单据'),
      dataIndex: 'filename',
      ellipsis: true,
      render: (name: string, c) => <Link to={`/documents/${c.document_id}`}>{name}</Link>,
    },
    { title: t('字段'), dataIndex: 'label', width: 160, ellipsis: true },
    {
      title: t('模型输出 → 人工修改'),
      render: (_, c) => (
        <span style={{ overflowWrap: 'anywhere' }}>
          <Typography.Text delete type="secondary">{c.before ?? t('（空）')}</Typography.Text> → <Typography.Text strong>{c.after ?? t('（空）')}</Typography.Text>
        </span>
      ),
    },
    { title: t('错误类型'), dataIndex: 'kind_name', width: 130, render: (n: string) => <Tag>{n}</Tag> },
    {
      title: t('出错环节'),
      width: 170,
      render: (_, c) => <StageCell c={c} canEdit={can('document:review')} />,
    },
    {
      title: t('已标黄'),
      dataIndex: 'flagged',
      width: 80,
      render: (f: boolean) => (f ? <Tag color="gold">{t('是')}</Tag> : <Tag color="red">{t('否')}</Tag>),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t('统计')}
        </Typography.Title>
        <Flex gap={8} wrap>
          <Select
            style={{ minWidth: 200 }}
            allowClear
            placeholder={t('全部模板')}
            value={template}
            onChange={setTemplate}
            options={(data?.templates ?? []).map((tpl) => ({ value: tpl.template, label: tpl.template_title }))}
          />
          <Select style={{ width: 130 }} value={days} onChange={setDays} options={RANGES} />
          {can('document:export') && <DatasetButton template={template} />}
        </Flex>
      </Flex>
      {stats.isError && <Alert type="error" showIcon title={t('无法读取统计数据')} description={String(stats.error)} />}
      {data && data.documents === 0 ? (
        <Card>
          <Empty description={t('暂无已完成审核的单据。审核完成后，此处按字段统计人工修改的次数及错误类型。')} />
        </Card>
      ) : (
        data && (
          <>
            <Row gutter={[16, 16]}>
              <Col flex="1 1 180px">
                <Card><Statistic title={t('已完成审核的单据')} value={data.documents} /></Card>
              </Col>
              <Col flex="1 1 180px">
                <Card><Statistic title={t('字段')} value={data.fields} suffix={<Typography.Text type="secondary" style={{ fontSize: 14 }}>{t('确认 {{confirmed}}', { confirmed: data.confirmed })}</Typography.Text>} /></Card>
              </Col>
              <Col flex="1 1 180px">
                <Card>
                  <Statistic title={t('人工修改率')} value={(data.rate * 100).toFixed(1)} suffix={t('%（{{edited}} 个）', { edited: data.edited })} />
                </Card>
              </Col>
              <Col flex="1 1 180px">
                <Card>
                  <Tooltip title={t('经人工修改但抽取时未标黄：系统未检出的错误，越少越好')}>
                    <Statistic
                      title={t('错误未标黄')}
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
                        ? t('{{timed_documents}} 份记录了用时。仅统计审核页处于前台且有操作的时间；页面在后台或一分钟无操作时不计入', { timed_documents: data.timed_documents })
                        : t('仅本次更新后审核的单据记录用时')
                    }
                  >
                    <Statistic title={t('平均审核用时')} value={reviewTime(data.review_seconds)} />
                  </Tooltip>
                </Card>
              </Col>
            </Row>
            <Card title={t('常见错误')} size="small">
              <Kinds kinds={data.kinds} />
            </Card>
            <Card
              title={
                <Tooltip title={t('依据证据判断：OCR 样本、本轮发送给模型的文字、审核时点击的原文或框选区域。证据不足时不下结论。可在下方“最近的修改”中展开查看理由或改判')}>
                  <span>{t('出错环节')}</span>
                </Tooltip>
              }
              size="small"
            >
              <StageSummary stages={data.stages ?? []} />
            </Card>
            <Card title={t('按模板')} size="small">
              <Table rowKey="template" size="small" pagination={false} columns={templateColumns} dataSource={data.templates} />
            </Card>
            <Card title={t('按字段（按修改次数降序）')} size="small">
              <Table
                rowKey={(f) => `${f.template}:${f.field}`}
                size="small"
                columns={fieldColumns}
                dataSource={data.by_field}
                pagination={{ pageSize: 15, hideOnSinglePage: true }}
              />
            </Card>
            <Card title={t('最近的修改')} size="small">
              <Table rowKey={(c) => `${c.document_id}:${c.label}:${c.at}`} size="small" pagination={false} columns={recentColumns} dataSource={data.recent} />
            </Card>
          </>
        )
      )}
    </Flex>
  )
}

/** Review time formatted as "1 min 25 s"; "—" when not recorded. */
function reviewTime(seconds: number | null): string {
  if (seconds == null) return '—'
  const total = Math.round(seconds)
  if (total < 60) return t('{{total}} 秒', { total })
  return t('{{floor}} 分 {{value}} 秒', { floor: Math.floor(total / 60), value: total % 60 })
}
