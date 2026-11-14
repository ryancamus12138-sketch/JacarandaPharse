import {
  ArrowLeftOutlined,
  BorderOutlined,
  CheckOutlined,
  DeleteOutlined,
  RightOutlined,
  SaveOutlined,
  StopOutlined,
  UndoOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Checkbox,
  Empty,
  Flex,
  Input,
  List,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { DET_STATUS, type DetBox, type DetPage, getDetPage, listDetPages, saveDetPage } from '../../api/ocrSamples'
import { useAuth } from '../../auth/useAuth'
import { usePageImage } from '../review/usePageImage'

type Point = [number, number]

/** 框的颜色：从哪来的。看不清的是灰色虚线。 */
const ORIGIN: Record<DetBox['origin'], { name: string; color: string }> = {
  ocr: { name: 'OCR 认的', color: '#1677ff' },
  sample: { name: '审核时改对的', color: '#52c41a' },
  missed: { name: '审核时框的漏认', color: '#fa8c16' },
  manual: { name: '这里画的', color: '#722ed1' },
}
const HISTORY = 50
const DONE = { confirm: '确认了这一页', exclude: '这一页不用了', reopen: '改回没确认' } as const
const HANDLE = 2.5
/** 角上的圆点半径：图长边的 1/600 乘这个数（图越大，圆点按比例越大，屏幕上看起来差不多）。 */

type Drag =
  | { kind: 'corner'; index: number; corner: number }
  | { kind: 'move'; index: number; start: Point; original: Point[] }
  | { kind: 'new'; start: Point; end: Point }

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function rect(a: Point, b: Point): Point[] {
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])]
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])]
  return [
    [x0, y0],
    [x1, y0],
    [x1, y1],
    [x0, y1],
  ]
}

function clamp(p: Point, width: number, height: number): Point {
  return [Math.min(Math.max(p[0], 0), width), Math.min(Math.max(p[1], 0), height)]
}

/**
 * 检测样本的框编辑器（ADR 0061）：照 PaddleOCR 的标注工具 PPOCRLabel 的做法——
 * 先用 OCR 结果预标注，人逐页调整（拖四个角、拖整个框、画新框、删框、改字、标看不清），确认这一页。
 *
 * 快捷键：N 画新框，Delete 删掉选中的框，Ctrl/⌘ + S 保存，Ctrl/⌘ + Z 撤销。
 */
export function DetEditorPage() {
  const params = useParams()
  const documentId = params.documentId ?? ''
  const pageNo = Number(params.page ?? '1')
  const detail = useQuery({
    queryKey: ['det-page', documentId, pageNo],
    queryFn: () => getDetPage(documentId, pageNo),
    staleTime: 0,
  })
  if (detail.isError) {
    return (
      <Flex vertical gap={16}>
        <Link to="/ocr-samples?tab=det">
          <ArrowLeftOutlined /> 检测样本
        </Link>
        <Alert type="error" showIcon title="这一页打不开" description={errorText(detail.error)} />
      </Flex>
    )
  }
  if (!detail.data) {
    return (
      <Flex justify="center" style={{ padding: 64 }}>
        <Spin />
      </Flex>
    )
  }
  // 换了一页、或者保存后修订号变了：重新从后端的框开始（撤销记录也清空）
  return <DetEditor key={`${documentId}:${pageNo}:${detail.data.revision}`} page={detail.data} documentId={documentId} pageNo={pageNo} />
}

function DetEditor({ page, documentId, pageNo }: { page: DetPage; documentId: string; pageNo: number }) {
  const navigate = useNavigate()
  const { message, modal } = App.useApp()
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const canEdit = can('value:review')
  const image = usePageImage(documentId, pageNo)
  const queue = useQuery({ queryKey: ['det-pages', 'todo'], queryFn: () => listDetPages('', 0, 200) })

  const [boxes, setBoxes] = useState<DetBox[]>(page.boxes)
  const [selected, setSelected] = useState<number | null>(null)
  const [drawing, setDrawing] = useState(false)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dirty, setDirty] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [canUndo, setCanUndo] = useState(false)
  const history = useRef<DetBox[][]>([])
  const svg = useRef<SVGSVGElement>(null)
  const textInput = useRef<HTMLInputElement>(null)
  const revision = page.revision

  // 有没存的改动时，关网页先问一声
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const remember = useCallback((old: DetBox[]) => {
    history.current = [...history.current.slice(-HISTORY + 1), old]
    setCanUndo(true)
  }, [])

  const change = useCallback(
    (next: DetBox[]) => {
      remember(boxes)
      setBoxes(next)
      setDirty(true)
    },
    [boxes, remember],
  )

  const undo = useCallback(() => {
    const last = history.current.pop()
    setCanUndo(history.current.length > 0)
    if (last) {
      setBoxes(last)
      setSelected(null)
      setDirty(true)
    }
  }, [])

  const save = useMutation({
    mutationFn: (action?: 'confirm' | 'exclude' | 'reopen') =>
      saveDetPage(documentId, pageNo, { revision, boxes }, action),
    onSuccess: (saved: DetPage, action) => {
      setDirty(false)
      void message.success(
        action ? DONE[action] : '保存了',
      )
      void queryClient.invalidateQueries({ queryKey: ['det-pages'] })
      queryClient.setQueryData(['det-page', documentId, pageNo], saved)
    },
    onError: (err) => void message.error(errorText(err)),
  })

  const width = page.width || 1
  const height = page.height || 1
  const locked = !canEdit || page.status === 'confirmed' || page.status === 'excluded'

  const removeSelected = useCallback(() => {
    if (selected === null || locked) return
    change(boxes.filter((_, i) => i !== selected))
    setSelected(null)
  }, [boxes, change, locked, selected])

  // 快捷键（在输入框里打字时不算）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (!locked && dirty) save.mutate(undefined)
        return
      }
      if (typing) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        removeSelected()
      } else if (e.key.toLowerCase() === 'n' && !locked) {
        setDrawing(true)
      } else if (e.key === 'Escape') {
        setDrawing(false)
        setSelected(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, locked, removeSelected, save, undo])

  function toImage(e: ReactPointerEvent): Point {
    const matrix = svg.current?.getScreenCTM()
    if (!matrix) return [0, 0]
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse())
    return clamp([p.x, p.y], width, height)
  }

  function begin(e: ReactPointerEvent, next: Drag) {
    if (locked) return
    e.stopPropagation()
    e.preventDefault()
    svg.current?.setPointerCapture(e.pointerId)
    remember(boxes)
    setDrag(next)
  }

  function move(e: ReactPointerEvent) {
    if (!drag) return
    const p = toImage(e)
    if (drag.kind === 'new') {
      setDrag({ ...drag, end: p })
      return
    }
    setBoxes((old) =>
      old.map((b, i) => {
        if (i !== drag.index) return b
        if (drag.kind === 'corner') {
          const points = b.points.map((q, k) => (k === drag.corner ? p : q)) as Point[]
          return { ...b, points }
        }
        const dx = p[0] - drag.start[0]
        const dy = p[1] - drag.start[1]
        return { ...b, points: drag.original.map(([x, y]) => clamp([x + dx, y + dy], width, height)) }
      }),
    )
    setDirty(true)
  }

  function end() {
    if (drag?.kind === 'new') {
      const points = rect(drag.start, drag.end)
      const big = points[2][0] - points[0][0] >= 4 && points[2][1] - points[0][1] >= 4
      if (big) {
        setBoxes((old) => [...old, { points, text: '', ignore: false, origin: 'manual' }])
        setSelected(boxes.length)
        setDirty(true)
        setDrawing(false)
        setTimeout(() => textInput.current?.focus(), 0)
      } else {
        history.current.pop()
        setCanUndo(history.current.length > 0)
      }
    }
    setDrag(null)
  }

  function update(index: number, patch: Partial<DetBox>) {
    change(boxes.map((b, i) => (i === index ? { ...b, ...patch } : b)))
  }

  const todo = (queue.data?.items ?? []).filter((p) => p.status === 'none' || p.status === 'draft')
  const next = todo.find((p) => !(p.document_id === documentId && p.page === pageNo))
  function goNext() {
    if (!next) return
    const go = () => navigate(`/ocr-samples/det/${next.document_id}/${next.page}`)
    if (dirty) {
      modal.confirm({ title: '这一页还没保存', content: '不保存就去下一页？', okText: '不保存，去下一页', onOk: go })
    } else {
      go()
    }
  }

  const status = page.status
  const current = selected !== null ? boxes[selected] : null
  const preview = drag?.kind === 'new' ? rect(drag.start, drag.end) : null
  const stroke = Math.max(width, height) / 600

  return (
    <Flex vertical gap={12} style={{ height: 'calc(100vh - 120px)' }}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Space wrap>
          <Link to="/ocr-samples?tab=det">
            <ArrowLeftOutlined /> 检测样本
          </Link>
          <Typography.Text strong>
            {page.filename} 第 {pageNo} 页
          </Typography.Text>
          <Tag color={DET_STATUS[status].color}>{DET_STATUS[status].name}</Tag>
          {dirty && <Tag color="red">没保存</Tag>}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {boxes.length} 个框{revision ? `，第 ${revision} 次修改` : '（预标注，还没存）'}
          </Typography.Text>
        </Space>
        <Space wrap>
          <Tooltip title="缩小">
            <Button icon={<ZoomOutOutlined />} onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} />
          </Tooltip>
          <Tooltip title="放大">
            <Button icon={<ZoomInOutlined />} onClick={() => setZoom((z) => Math.min(4, z + 0.25))} />
          </Tooltip>
          {!locked && (
            <>
              <Tooltip title="在图上拖一个矩形（快捷键 N）">
                <Button icon={<BorderOutlined />} type={drawing ? 'primary' : 'default'} onClick={() => setDrawing((d) => !d)}>
                  画新框
                </Button>
              </Tooltip>
              <Tooltip title="撤销（Ctrl/⌘ + Z）">
                <Button icon={<UndoOutlined />} disabled={!canUndo} onClick={undo} />
              </Tooltip>
              <Button icon={<SaveOutlined />} disabled={!dirty} loading={save.isPending && save.variables === undefined} onClick={() => save.mutate(undefined)}>
                保存
              </Button>
              <Button icon={<StopOutlined />} loading={save.isPending && save.variables === 'exclude'} onClick={() => save.mutate('exclude')}>
                不用这一页
              </Button>
              <Tooltip title="框都调好了：确认后才会导出（PPOCRLabel 的确认）">
                <Button type="primary" icon={<CheckOutlined />} loading={save.isPending && save.variables === 'confirm'} onClick={() => save.mutate('confirm')}>
                  确认这一页
                </Button>
              </Tooltip>
            </>
          )}
          {canEdit && locked && (
            <Button loading={save.isPending} onClick={() => save.mutate('reopen')}>
              重新打开
            </Button>
          )}
          <Button icon={<RightOutlined />} disabled={!next} onClick={goNext}>
            下一页{todo.length ? `（还有 ${todo.length - (todo.some((p) => p.document_id === documentId && p.page === pageNo) ? 1 : 0)}）` : ''}
          </Button>
        </Space>
      </Flex>

      <Flex gap={12} style={{ flex: 1, minHeight: 0 }}>
        <div style={{ flex: 1, overflow: 'auto', background: '#f0eef5', borderRadius: 8, padding: 12 }}>
          {image.isPending && (
            <Flex justify="center" style={{ padding: 64 }}>
              <Spin />
            </Flex>
          )}
          {image.data && (
            <div style={{ position: 'relative', width: `${zoom * 100}%`, margin: '0 auto' }}>
              <img
                src={image.data}
                alt={`第 ${pageNo} 页`}
                draggable={false}
                style={{ display: 'block', width: '100%', boxShadow: '0 1px 4px rgba(0,0,0,0.15)' }}
              />
              <svg
                ref={svg}
                viewBox={`0 0 ${width} ${height}`}
                preserveAspectRatio="none"
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '100%',
                  height: '100%',
                  cursor: drawing ? 'crosshair' : undefined,
                  touchAction: 'none',
                }}
                onPointerMove={move}
                onPointerUp={end}
              >
                <rect
                  width={width}
                  height={height}
                  fill="transparent"
                  onPointerDown={(e) => {
                    if (drawing) {
                      const p = toImage(e)
                      begin(e, { kind: 'new', start: p, end: p })
                    } else {
                      setSelected(null)
                    }
                  }}
                />
                {boxes.map((b, i) => {
                  const color = b.ignore ? '#8c8c8c' : ORIGIN[b.origin].color
                  const active = i === selected
                  return (
                    <polygon
                      key={i}
                      points={b.points.map(([x, y]) => `${x},${y}`).join(' ')}
                      fill={color}
                      fillOpacity={active ? 0.25 : 0.08}
                      stroke={color}
                      strokeWidth={active ? 3 : 1.5}
                      strokeDasharray={b.ignore ? '6 4' : undefined}
                      vectorEffect="non-scaling-stroke"
                      style={{ cursor: locked ? 'pointer' : 'move', pointerEvents: drawing ? 'none' : 'auto' }}
                      onPointerDown={(e) => {
                        setSelected(i)
                        begin(e, { kind: 'move', index: i, start: toImage(e), original: b.points as Point[] })
                      }}
                    >
                      <title>{b.ignore ? '看不清（###）' : b.text}</title>
                    </polygon>
                  )
                })}
                {current &&
                  !locked &&
                  current.points.map(([x, y], k) => (
                    <circle
                      key={k}
                      cx={x}
                      cy={y}
                      r={HANDLE * stroke}
                      fill="#fff"
                      stroke="#f5222d"
                      strokeWidth={2}
                      vectorEffect="non-scaling-stroke"
                      style={{ cursor: 'crosshair' }}
                      onPointerDown={(e) => begin(e, { kind: 'corner', index: selected!, corner: k })}
                    />
                  ))}
                {preview && (
                  <polygon
                    points={preview.map(([x, y]) => `${x},${y}`).join(' ')}
                    fill="#722ed1"
                    fillOpacity={0.15}
                    stroke="#722ed1"
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                    pointerEvents="none"
                  />
                )}
              </svg>
            </div>
          )}
        </div>

        <Flex vertical gap={12} style={{ width: 340, flexShrink: 0, minHeight: 0 }}>
          {current ? (
            <Flex vertical gap={8} style={{ background: '#fff', padding: 12, borderRadius: 8 }}>
              <Space>
                <Typography.Text strong>第 {selected! + 1} 个框</Typography.Text>
                <Tag color={ORIGIN[current.origin].color}>{ORIGIN[current.origin].name}</Tag>
              </Space>
              <Input
                ref={(el) => {
                  textInput.current = el?.input ?? null
                }}
                value={current.text}
                disabled={locked || current.ignore}
                placeholder="图上原样的文字（一个字都不改）"
                onChange={(e) => update(selected!, { text: e.target.value })}
              />
              <Checkbox
                checked={current.ignore}
                disabled={locked}
                onChange={(e) => update(selected!, { ignore: e.target.checked })}
              >
                看不清（导出成 ###，不参与训练）
              </Checkbox>
              {!locked && (
                <Button danger icon={<DeleteOutlined />} onClick={removeSelected}>
                  删掉这个框（Delete）
                </Button>
              )}
            </Flex>
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              点一个框改字；拖框整体移动，拖红色圆点改形状。漏了的字点"画新框"（N）补上，
              框错了的按 Delete 删掉。看不清的勾"看不清"。每一行字一个框，框贴着字。
            </Typography.Text>
          )}
          <Flex gap={6} wrap>
            {(Object.keys(ORIGIN) as DetBox['origin'][]).map((o) => (
              <Tag key={o} color={ORIGIN[o].color}>
                {ORIGIN[o].name} {boxes.filter((b) => b.origin === o).length}
              </Tag>
            ))}
          </Flex>
          <div style={{ flex: 1, overflow: 'auto', background: '#fff', borderRadius: 8 }}>
            {boxes.length === 0 ? (
              <Empty description="这一页还没有框" style={{ padding: 24 }} />
            ) : (
              <List
                size="small"
                dataSource={boxes.map((b, i) => ({ b, i }))}
                renderItem={({ b, i }) => (
                  <List.Item
                    onClick={() => setSelected(i)}
                    style={{ cursor: 'pointer', background: i === selected ? '#f0f5ff' : undefined, paddingInline: 12 }}
                  >
                    <Typography.Text
                      type={b.ignore ? 'secondary' : undefined}
                      delete={b.ignore}
                      ellipsis
                      style={{ maxWidth: 280, color: !b.ignore && !b.text.trim() ? '#f5222d' : undefined }}
                    >
                      {i + 1}. {b.ignore ? '看不清' : b.text || '（还没填字）'}
                    </Typography.Text>
                  </List.Item>
                )}
              />
            )}
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            预标注：{page.ocr_engine || 'OCR'}
          </Typography.Text>
        </Flex>
      </Flex>
    </Flex>
  )
}
