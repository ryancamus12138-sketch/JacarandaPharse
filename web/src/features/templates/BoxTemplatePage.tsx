import { AimOutlined, ArrowLeftOutlined, DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Flex,
  Input,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  type TableProps,
  Tooltip,
  Typography,
} from 'antd'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { describeRegion, getDocument, getPageOcr, getPageStructure, reprocessDocument } from '../../api/documents'
import { createFromBoxes } from '../../api/templates'
import type { Box, FieldType, Template, TemplateField } from '../../api/types'
import { usePageImage } from '../review/usePageImage'
import { blankField, CATEGORIES, fieldsProblem, TYPE_OPTIONS } from './templateFields'

interface Boxed extends TemplateField {
  key: number
  page: number
  box: Box
  sample: string
  busy: boolean
}

/** 从标签猜一个字段 ID：英文就用它（Invoice No. → invoice_no），中文不猜（空的，用自动编号）。 */
function guessId(label: string): string {
  const id = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
  return /^[a-z][a-z0-9_]*$/.test(id) ? id : ''
}

/** 两个框是不是同一块（加过的值不再显示虚线框）。 */
function sameBox(a: Box, b: Box): boolean {
  return a.every((v, i) => Math.abs(v - b[i]) < 2)
}

const COLORS = ['#722ed1', '#13c2c2', '#fa8c16', '#2f54eb', '#eb2f96', '#52c41a']

/**
 * 在样例上框选建模板（ADR 0047）：在单据原图上框住一个值，系统读出框里的字，猜出旁边的标签、类型、
 * 在页面哪里；起好中文名和字段 ID，保存成"我的模板"。标签记成别名，位置写进字段说明，模型更容易找到。
 */
export function BoxTemplatePage() {
  const { documentId = '' } = useParams()
  const navigate = useNavigate()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const doc = useQuery({ queryKey: ['document', documentId], queryFn: () => getDocument(documentId) })
  const [page, setPage] = useState(1)
  const image = usePageImage(documentId, page)
  const [natural, setNatural] = useState<[number, number] | null>(null)
  const [fields, setFields] = useState<Boxed[]>([])
  const [draft, setDraft] = useState<Box | null>(null)
  const start = useRef<[number, number] | null>(null)
  const nextKey = useRef(1)
  const [title, setTitle] = useState('')
  /** 正在给哪一行选标签：在原图上点一下标签文字。 */
  const [picking, setPicking] = useState<number | null>(null)
  const structure = useQuery({
    queryKey: ['page-structure', documentId, page],
    queryFn: () => getPageStructure(documentId, page),
  })
  const ocr = useQuery({ queryKey: ['page-ocr', documentId, page], queryFn: () => getPageOcr(documentId, page) })
  const pairs = structure.data?.pairs ?? []
  const [category, setCategory] = useState('其他')

  const pageCount = doc.data?.page_count ?? 1
  const update = (key: number, patch: Partial<Boxed>) =>
    setFields((fs) => fs.map((f) => (f.key === key ? { ...f, ...patch } : f)))

  /** 屏幕上的点 → 原图上的像素（和 OCR、出处框一样的坐标）。 */
  function toImage(e: React.PointerEvent<HTMLDivElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect()
    const [w, h] = natural ?? [rect.width, rect.height]
    return [((e.clientX - rect.left) / rect.width) * w, ((e.clientY - rect.top) / rect.height) * h]
  }

  /** 加一个字段。key：版面分析配好的标题（点"读到的键值对"加的），没有就看接口查到的。 */
  async function addBox(box: Box, key?: string) {
    const id = nextKey.current++
    const used = fields.map((f) => f.name)
    const base = { ...blankField(fields), key: id, page, box, sample: '', busy: true }
    setFields((fs) => [...fs, base])
    try {
      const found = await describeRegion(documentId, page, box)
      const label = key ?? found.label
      let name = (label && guessId(label)) || found.name || base.name
      const stem = name
      for (let i = 2; used.includes(name); i++) name = `${stem}_${i}`
      update(id, {
        busy: false,
        sample: found.text,
        label: label ?? '',
        name,
        type: found.type as FieldType,
        description: found.description,
        aliases: label ? [label] : [],
      })
    } catch (err) {
      update(id, { busy: false })
      void message.error(err instanceof ApiError ? err.message : String(err))
    }
  }

  const save = useMutation({
    mutationFn: () =>
      createFromBoxes({
        document_id: documentId,
        title: title.trim(),
        category,
        description: `在样例「${doc.data?.filename ?? ''}」上框选建的模板`,
        fields: fields.map(({ key: _k, sample: _s, busy: _b, ...f }) => f),
      }),
    onSuccess: (template: Template) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      modal.confirm({
        title: `已存成模板「${template.title}」`,
        content: '要不要用新模板重新处理这份样例，看看模型按新模板抽得对不对？',
        okText: '用新模板重新处理',
        cancelText: '去编辑模板',
        onOk: async () => {
          await reprocessDocument(documentId, {
            template: template.id ?? undefined,
            reason: 'template_changed',
            note: '框选建模板后用新模板处理样例',
          })
          void queryClient.invalidateQueries({ queryKey: ['document', documentId] })
          navigate(`/documents/${documentId}`)
        },
        onCancel: () => navigate(template.id ? `/templates/${template.id}` : '/templates'),
      })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const problem =
    (!title.trim() ? '给模板起个名字' : null) ??
    (fields.length === 0 ? '先在原图上框出至少一个值' : null) ??
    fieldsProblem(fields, '')

  const columns: TableProps<Boxed>['columns'] = [
    {
      title: '',
      width: 14,
      render: (_, f) => <span style={{ display: 'inline-block', width: 10, height: 10, background: COLORS[(f.key - 1) % COLORS.length] }} />,
    },
    {
      title: <Tooltip title="审核页、导出的 Excel 里显示的名字，中英文都行">名称</Tooltip>,
      width: 200,
      render: (_, f) => (
        <Space.Compact size="small" style={{ width: '100%' }}>
          <Input
            value={f.label}
            status={!f.busy && !f.label ? 'warning' : undefined}
            placeholder={f.busy ? '' : '没找到标签'}
            onChange={(e) => update(f.key, { label: e.target.value })}
          />
          <Tooltip title="在原图上点一下标签文字，用它当名称">
            <Button
              type={picking === f.key ? 'primary' : 'default'}
              icon={<AimOutlined />}
              aria-label="在原图上选标签"
              onClick={() => setPicking(picking === f.key ? null : f.key)}
            />
          </Tooltip>
        </Space.Compact>
      ),
    },
    {
      title: <Tooltip title="程序里用的代号：小写英文字母开头，只能有字母、数字、下划线">字段 ID</Tooltip>,
      width: 150,
      render: (_, f) => <Input size="small" value={f.name} onChange={(e) => update(f.key, { name: e.target.value })} />,
    },
    {
      title: '类型',
      width: 130,
      render: (_, f) => (
        <Select size="small" style={{ width: '100%' }} value={f.type} options={TYPE_OPTIONS} onChange={(type: FieldType) => update(f.key, { type })} />
      ),
    },
    {
      title: '必填',
      width: 56,
      render: (_, f) => <Checkbox checked={f.required} onChange={(e) => update(f.key, { required: e.target.checked })} />,
    },
    {
      title: '样例里的值',
      render: (_, f) => (f.busy ? <Spin size="small" /> : <Typography.Text type="secondary" ellipsis style={{ maxWidth: 160 }}>{f.sample || '（没认出字）'}</Typography.Text>),
    },
    {
      title: '',
      width: 40,
      render: (_, f) => (
        <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label="删掉" onClick={() => setFields((fs) => fs.filter((x) => x.key !== f.key))} />
      ),
    },
  ]

  if (doc.isPending) return <Spin />
  if (doc.isError) return <Alert type="error" showIcon title="打不开这份单据" />

  return (
    <Flex vertical gap={16}>
      <Flex align="center" gap={12} wrap>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/documents/${documentId}`)}>
          返回单据
        </Button>
        <Typography.Title level={4} style={{ margin: 0 }}>
          在样例上框选建模板
        </Typography.Title>
        <Typography.Text type="secondary">{doc.data.filename}</Typography.Text>
      </Flex>
      <Alert
        type="info"
        showIcon
        title="用一张样例单据，告诉系统这类单据要抽哪些值"
        description={
          <ol style={{ margin: 0, paddingInlineStart: 20 }}>
            <li>原图上虚线框住的是系统读到的值，点一下就加进模板，名称自动用它的标签；右边也列出了全部，可以一个个加。</li>
            <li>没有虚线框的值，按住鼠标自己拖一个框（框值，不框标签）。没找到标签的，名称会空着：填一个，或点名称旁边的瞄准，再点原图上的标签文字。</li>
            <li>要的值都框完，在右边上面给模板起个名字，点“存成模板”。以后这类单据用这个模板，模型就按这些字段抽。</li>
          </ol>
        }
      />
      <Flex gap={16} align="flex-start" wrap>
        <Card size="small" style={{ flex: '1 1 420px', minWidth: 320 }} title={
          pageCount > 1 ? (
            <Segmented value={page} onChange={(p) => { setPage(Number(p)); setNatural(null) }} options={Array.from({ length: pageCount }, (_, i) => ({ value: i + 1, label: `第 ${i + 1} 页` }))} />
          ) : '原图'
        }>
          {image.data ? (
            <div
              style={{ position: 'relative', cursor: 'crosshair', touchAction: 'none', userSelect: 'none' }}
              onPointerDown={(e) => {
                if (picking !== null) {
                  // 选标签：点到哪块文字，就用它当这一行的名称和别名
                  const [x, y] = toImage(e)
                  const hit = ocr.data?.lines.find(
                    (l) => l.box[0] <= x && x <= l.box[2] && l.box[1] <= y && y <= l.box[3],
                  )
                  if (hit) {
                    const label = hit.text.replace(/[\s:：*#]+$/, '')
                    update(picking, { label, aliases: [label], name: guessId(label) || fields.find((f) => f.key === picking)?.name || '' })
                    setPicking(null)
                  }
                  return
                }
                e.currentTarget.setPointerCapture(e.pointerId)
                start.current = toImage(e)
                setDraft(null)
              }}
              onPointerMove={(e) => {
                if (!start.current) return
                const [x, y] = toImage(e)
                const [sx, sy] = start.current
                setDraft([Math.min(sx, x), Math.min(sy, y), Math.max(sx, x), Math.max(sy, y)])
              }}
              onPointerUp={() => {
                start.current = null
                if (draft && draft[2] - draft[0] > 6 && draft[3] - draft[1] > 6) void addBox(draft)
                setDraft(null)
              }}
            >
              <img
                src={image.data}
                alt="样例原图"
                draggable={false}
                style={{ width: '100%', display: 'block' }}
                onLoad={(e) => setNatural([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
              />
              {natural &&
                picking === null &&
                pairs
                  .filter((p) => !fields.some((f) => f.page === page && sameBox(f.box, p.value_box)))
                  .map((p, i) => {
                    const [x0, y0, x1, y1] = p.value_box
                    return (
                      <Tooltip key={`pair-${i}`} title={`${p.key}：${p.value}（点一下加进模板）`}>
                        <div
                          onPointerDown={(e) => e.stopPropagation()}
                          onClick={() => void addBox(p.value_box, p.key)}
                          style={{
                            position: 'absolute',
                            left: `${(x0 / natural[0]) * 100}%`,
                            top: `${(y0 / natural[1]) * 100}%`,
                            width: `${((x1 - x0) / natural[0]) * 100}%`,
                            height: `${((y1 - y0) / natural[1]) * 100}%`,
                            border: '1.5px dashed #13a8a8',
                            background: '#13a8a814',
                            cursor: 'pointer',
                          }}
                        />
                      </Tooltip>
                    )
                  })}
              {natural &&
                [...fields.filter((f) => f.page === page), ...(draft ? [{ key: 0, box: draft, label: '' }] : [])].map((f) => {
                  const [x0, y0, x1, y1] = f.box
                  const color = f.key ? COLORS[(f.key - 1) % COLORS.length] : '#999'
                  return (
                    <div
                      key={f.key}
                      style={{
                        position: 'absolute',
                        left: `${(x0 / natural[0]) * 100}%`,
                        top: `${(y0 / natural[1]) * 100}%`,
                        width: `${((x1 - x0) / natural[0]) * 100}%`,
                        height: `${((y1 - y0) / natural[1]) * 100}%`,
                        border: `2px solid ${color}`,
                        background: `${color}22`,
                        pointerEvents: 'none',
                      }}
                    >
                      {f.label && (
                        <span style={{ position: 'absolute', top: -20, left: -2, background: color, color: '#fff', fontSize: 11, padding: '0 4px', whiteSpace: 'nowrap' }}>
                          {f.label}
                        </span>
                      )}
                    </div>
                  )
                })}
            </div>
          ) : (
            <Spin />
          )}
        </Card>
        <Card size="small" style={{ flex: '1 1 520px', minWidth: 360 }} title="框好的字段">
          <Flex vertical gap={12}>
            <Flex gap={8} wrap>
              <Input
                style={{ flex: 1, minWidth: 200 }}
                placeholder="模板名称，例如 某某供应商送货单"
                status={fields.length > 0 && !title.trim() ? 'warning' : undefined}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Select style={{ width: 120 }} value={category} onChange={setCategory} options={CATEGORIES.map((c) => ({ value: c, label: c }))} />
            </Flex>
            {pairs.length > 0 && (
              <Flex vertical gap={4} style={{ maxHeight: 220, overflow: 'auto' }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  读到的键值对（第 {page} 页）：
                </Typography.Text>
                {pairs.map((p, i) => {
                  const added = fields.some((f) => f.page === page && sameBox(f.box, p.value_box))
                  return (
                    <Flex key={i} gap={8} align="center">
                      <Typography.Text strong style={{ flexShrink: 0 }}>{p.key}</Typography.Text>
                      <Typography.Text type="secondary" ellipsis style={{ flex: 1, minWidth: 0 }}>
                        {p.value}
                      </Typography.Text>
                      {added ? (
                        <Tag style={{ marginInlineEnd: 0 }}>已添加</Tag>
                      ) : (
                        <Button size="small" icon={<PlusOutlined />} onClick={() => void addBox(p.value_box, p.key)}>
                          添加
                        </Button>
                      )}
                    </Flex>
                  )
                })}
              </Flex>
            )}
            {picking !== null && (
              <Alert type="info" showIcon title="在左边原图上点一下标签文字" action={<Button size="small" onClick={() => setPicking(null)}>取消</Button>} />
            )}
            {fields.length === 0 ? (
              <Empty description="还没有字段。点原图上的虚线框，或者自己拖一个框。" />
            ) : (
              <Table rowKey="key" size="small" pagination={false} scroll={{ x: 760 }} columns={columns} dataSource={fields} expandable={{
                expandedRowRender: (f) => (
                  <Flex vertical gap={6}>
                    <Input size="small" addonBefore="说明" value={f.description} onChange={(e) => update(f.key, { description: e.target.value })} />
                    <Input size="small" addonBefore="别名" placeholder="单据上的其他写法，用逗号分开" value={f.aliases.join('，')} onChange={(e) => update(f.key, { aliases: e.target.value.split(/[,，]/).map((a) => a.trim()).filter(Boolean) })} />
                  </Flex>
                ),
              }} />
            )}
            {problem && fields.length > 0 && <Typography.Text type="warning">{problem}</Typography.Text>}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              说明会告诉模型这个值在单据哪里，别名是单据上标签的写法；展开一行可以改。表格（商品明细）请保存后在模板编辑页里加。
            </Typography.Text>
            <Flex justify="flex-end">
              <Button type="primary" icon={<SaveOutlined />} disabled={!!problem || fields.some((f) => f.busy)} loading={save.isPending} onClick={() => save.mutate()}>
                存成模板
              </Button>
            </Flex>
          </Flex>
        </Card>
      </Flex>
    </Flex>
  )
}
