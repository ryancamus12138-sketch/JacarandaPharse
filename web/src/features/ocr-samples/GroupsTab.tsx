import { ApartmentOutlined, DisconnectOutlined, LinkOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Image,
  Modal,
  Popconfirm,
  Select,
  Table,
  type TableProps,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import {
  type PairResult,
  type SampleGroup,
  SPLITS,
  type Split,
  type Unit,
  decidePair,
  detachMember,
  findUnits,
  listGroups,
  listPairs,
  setGroupSplit,
} from '../../api/ocrSamples'
import { usePageImage } from '../review/usePageImage'
import { useTranslation } from 'react-i18next'

const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }
const SPLIT_ORDER = Object.keys(SPLITS) as Split[]

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

/** A business document: page image (click to enlarge), file name and the values used for pairing. */
function UnitSide({ unit }: { unit: Unit }) {
  const { t } = useTranslation()
  const image = usePageImage(unit.document_id, unit.page)
  return (
    <Flex vertical gap={8} style={{ flex: 1, minWidth: 0 }}>
      <div style={{ height: 240, background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'hidden' }}>
        {image.data && (
          <Image
            src={image.data}
            alt={unit.filename}
            height={238}
            width="100%"
            style={{ objectFit: 'contain' }}
          />
        )}
      </div>
      <Typography.Text strong ellipsis={{ tooltip: unit.filename }}>
        <Link to={`/documents/${unit.document_id}`}>{unit.filename}</Link>
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {unit.page === unit.last_page ? t('第 {{page}} 页', { page: unit.page }) : t('第 {{page}}–{{last_page}} 页', { page: unit.page, last_page: unit.last_page })} · {unit.template}
        {unit.samples > 0 ? t(' · {{samples}} 条样本', { samples: unit.samples }) : t(' · 还没有样本')}
      </Typography.Text>
      <Descriptions
        size="small"
        column={1}
        items={Object.entries(unit.values).map(([k, v]) => ({ key: k, label: k, children: v }))}
      />
    </Flex>
  )
}

/** Two documents side by side, to confirm against the images whether they are the same. */
function PairView({
  a,
  b,
  busy,
  onDecide,
}: {
  a: Unit
  b: Unit
  busy: boolean
  onDecide: (decision: 'same' | 'different') => void
}) {
  const { t } = useTranslation()
  return (
    <Flex vertical gap={12}>
      <Flex gap={16}>
        <UnitSide unit={a} />
        <UnitSide unit={b} />
      </Flex>
      <Flex gap={8} justify="flex-end">
        <Button onClick={() => onDecide('different')} disabled={busy}>
          {t('不是同一张')}
        </Button>
        <Button type="primary" icon={<LinkOutlined />} loading={busy} onClick={() => onDecide('same')}>
          {t('是同一张单据')}
        </Button>
      </Flex>
    </Flex>
  )
}

/** Find a business document by file name. */
function UnitPicker({ value, onChange }: { value: Unit | null; onChange: (unit: Unit | null) => void }) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const found = useQuery({
    queryKey: ['ocr-units', q],
    queryFn: () => findUnits(q),
    enabled: q.trim().length > 0,
  })
  const options = (found.data ?? []).map((u) => ({
    value: `${u.document_id}:${u.page}`,
    label: t('{{filename}} · 第 {{page}} 页 · {{template}}', { filename: u.filename, page: u.page, template: u.template }),
    unit: u,
  }))
  return (
    <Select
      showSearch
      allowClear
      style={{ width: '100%' }}
      placeholder={t('输入文件名找单据')}
      filterOption={false}
      onSearch={setQ}
      loading={found.isFetching}
      value={value ? `${value.document_id}:${value.page}` : undefined}
      onChange={(key) => onChange(options.find((o) => o.value === key)?.unit ?? null)}
      options={options}
      notFoundContent={q ? t('没有找到') : t('输入文件名')}
    />
  )
}

/** Document groups: possible pairs (PDF and photo) awaiting confirmation, and the split of each group. */
export function GroupsTab({ canChange, canReview }: { canChange: boolean; canReview: boolean }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const groups = useQuery({ queryKey: ['ocr-groups'], queryFn: listGroups })
  const pairs = useQuery({ queryKey: ['ocr-pairs'], queryFn: listPairs })
  const [manual, setManual] = useState(false)
  const [left, setLeft] = useState<Unit | null>(null)
  const [right, setRight] = useState<Unit | null>(null)
  const refresh = () => {
    for (const key of ['ocr-groups', 'ocr-pairs', 'ocr-samples', 'ocr-sample-preview', 'ocr-exports']) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }
  const done = (result: PairResult) => {
    refresh()
    if (result.quarantined) void message.warning(result.message, 6)
    else void message.success(result.message)
  }
  const decide = useMutation({
    mutationFn: ({ a, b, decision }: { a: Unit; b: Unit; decision: 'same' | 'different' }) =>
      decidePair(a, b, decision),
    onSuccess: (result) => {
      done(result)
      setManual(false)
      setLeft(null)
      setRight(null)
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const detach = useMutation({
    mutationFn: detachMember,
    onSuccess: done,
    onError: (err) => void message.error(errorText(err)),
  })
  const change = useMutation({
    mutationFn: ({ group, split }: { group: string; split: Split }) => setGroupSplit(group, split),
    onSuccess: () => {
      refresh()
      void message.success(t('已改'))
    },
    onError: (err) => void message.error(errorText(err)),
  })

  const data = groups.data
  const columns: TableProps<SampleGroup>['columns'] = [
    {
      title: t('组号'),
      dataIndex: 'code',
      width: 150,
      render: (code: string) => (
        <Typography.Text style={MONO} copyable>
          {code}
        </Typography.Text>
      ),
    },
    {
      title: t('分在'),
      key: 'split',
      width: 200,
      render: (_, g) => (
        <Flex vertical gap={4} align="flex-start">
          {canChange ? (
            <Select
              size="small"
              style={{ width: 140 }}
              value={g.split}
              loading={change.isPending && change.variables?.group === g.group_id}
              onChange={(split) => change.mutate({ group: g.group_id, split })}
              options={SPLIT_ORDER.map((s) => ({ value: s, label: SPLITS[s].name }))}
              labelRender={({ value }) => (
                <Tag color={SPLITS[value as Split].color} style={{ marginInlineEnd: 0 }}>
                  {SPLITS[value as Split].name}
                </Tag>
              )}
            />
          ) : (
            <Tag color={SPLITS[g.split].color}>{SPLITS[g.split].name}</Tag>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {g.reason}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('业务单据'),
      key: 'members',
      render: (_, g) => (
        <Flex vertical gap={2}>
          {g.members.map((m) => (
            <Flex key={m.id} gap={6} align="center" wrap>
              <Link to={`/documents/${m.document_id}`}>{m.filename}</Link>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t('第 {{first_page}} 页起 · {{format}} · {{samples}} 条', { first_page: m.first_page, format: m.source_format === 'pdf' ? 'PDF' : t('照片'), samples: m.samples })}
              </Typography.Text>
              {canReview && g.members.length > 1 && (
                <Popconfirm
                  title={t('把这张从组里拆出来？')}
                  description={t('配错了才拆。这一组导出过的话，拆开后两边都会被隔离。')}
                  onConfirm={() => detach.mutate(m.id)}
                >
                  <Button size="small" type="link" icon={<DisconnectOutlined />}>
                    {t('拆开')}
                  </Button>
                </Popconfirm>
              )}
            </Flex>
          ))}
        </Flex>
      ),
    },
    { title: t('样本'), dataIndex: 'samples', width: 70, align: 'right' },
    {
      title: t('导出过'),
      dataIndex: 'exported_as',
      width: 160,
      render: (parts: string[]) =>
        parts.length === 0 ? (
          <Typography.Text type="secondary">{t('没有')}</Typography.Text>
        ) : (
          <Flex gap={4} wrap>
            {parts.map((p) => (
              <Tag key={p}>{SPLITS[p as Split]?.name ?? p}</Tag>
            ))}
          </Flex>
        ),
    },
  ]

  return (
    <Flex vertical gap={16}>
      <Alert
        type="info"
        showIcon
        icon={<ApartmentOutlined />}
        title={t('同一张单据的样本永远在同一边')}
        description={t('一张业务单据（文件里的一张单据：重新处理的几轮、每一页都算它）是一组。字节一样的文件自动同组；同一张单据的 PDF 和照片要人看原图确认后合并。第一次有样本时按哈希分边（约 80% 训练、20% 验证），之后不变。合并或拆开时如果已经导出过、可能一份训练一份验证，这一组会被隔离、不再导出，受影响的导出记录会记一笔。')}
      />
      {(pairs.data?.length ?? 0) > 0 && (
        <Card
          size="small"
          title={t('可能是同一张单据（{{length}}）', { length: pairs.data?.length })}
          extra={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('单据类型、单号、日期、金额、币种、发单方对得上；看原图确认')}
            </Typography.Text>
          }
        >
          {pairs.data && pairs.data.length > 0 && (
            <PairView
              key={`${pairs.data[0].a.document_id}-${pairs.data[0].b.document_id}`}
              a={pairs.data[0].a}
              b={pairs.data[0].b}
              busy={decide.isPending || !canReview}
              onDecide={(decision) => decide.mutate({ a: pairs.data[0].a, b: pairs.data[0].b, decision })}
            />
          )}
        </Card>
      )}
      <Card
        size="small"
        title={
          <Flex gap={12} align="center" wrap>
            <span>{t('分组')}</span>
            {data && (
              <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 'normal' }}>
                {t('划分版本 {{split_version}} ·', { split_version: data.split_version })}{' '}
                {SPLIT_ORDER.map((s) => t('{{name}} {{value}} 组', { name: SPLITS[s].name, value: data.counts[s] ?? 0 })).join(' · ')}
              </Typography.Text>
            )}
          </Flex>
        }
        extra={
          canReview && (
            <Button size="small" icon={<LinkOutlined />} onClick={() => setManual(true)}>
              {t('手动配对')}
            </Button>
          )
        }
      >
        {groups.isError && <Alert type="error" showIcon title={errorText(groups.error)} />}
        <Table
          rowKey="group_id"
          size="middle"
          loading={groups.isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
          scroll={{ x: 900 }}
          locale={{ emptyText: <Empty description={t('还没有样本，也就还没有分组')} /> }}
        />
      </Card>
      <Modal
        title={t('手动配对：这两张是同一张单据')}
        open={manual}
        onCancel={() => setManual(false)}
        width={880}
        footer={null}
        destroyOnHidden
      >
        <Flex vertical gap={12}>
          <Flex gap={16}>
            <div style={{ flex: 1 }}>
              <UnitPicker value={left} onChange={setLeft} />
            </div>
            <div style={{ flex: 1 }}>
              <UnitPicker value={right} onChange={setRight} />
            </div>
          </Flex>
          {left && right ? (
            <PairView
              a={left}
              b={right}
              busy={decide.isPending}
              onDecide={(decision) => decide.mutate({ a: left, b: right, decision })}
            />
          ) : (
            <Typography.Text type="secondary">
              {t('两边各选一张单据（同一张单据的 PDF 和照片、重新扫描的……），并排看原图确认。')}
            </Typography.Text>
          )}
        </Flex>
      </Modal>
    </Flex>
  )
}
