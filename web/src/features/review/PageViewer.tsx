import {
  SelectOutlined,
  ColumnHeightOutlined,
  FontSizeOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons'
import { Alert, Button, Flex, Spin, Tooltip, Typography } from 'antd'
import {
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from 'react'
import { ApiError } from '../../api/client'
import type { Box, FieldValue, PageOcr } from '../../api/types'
import { boxColor, evidenceOf, fieldLabel, splitCell } from './fields'
import { usePageImage } from './usePageImage'

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3]
const PAD = 4 // 框比文字稍大一点，不压住字
const FRAME_PADDING = 16

interface Props {
  documentId: string
  page: number
  values: FieldValue[]
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** 这一页的 OCR 结果（"从原文选取"用）。还没取到是 undefined。 */
  ocr: PageOcr | undefined
  /** 显示原文文字块：按住 Alt，或者点了工具栏的"原文文字"。 */
  showOcr: boolean
  onToggleOcr: () => void
  /** 正在多选的文字块（按住 Shift 或 ⌘/Ctrl 点的）。 */
  picked: number[]
  /** 点了一个文字块。multi：按着 Shift 或 ⌘/Ctrl，是多选。 */
  onPickLine: (index: number, multi: boolean) => void
  /** 能不能框选（有"修改字段"权限）。 */
  canDraw: boolean
  /** 框选模式：在图上拖出一个框，或者拖动、拉伸选中字段的框。 */
  drawOn: boolean
  onToggleDraw: () => void
  /** 正在画、或者画好了还没用的框。 */
  draft: Box | null
  /** 拖动过程中框一直在变；null 是取消。 */
  onDraft: (box: Box | null) => void
  /** 松开鼠标，框画好了。 */
  onDraftDone: (box: Box) => void
  /** 工具栏最前面多放的按钮（例如"确认"）。 */
  extra?: ReactNode
}

type Handle = 'new' | 'move' | 'nw' | 'ne' | 'sw' | 'se'

/** 把框整理成 左 < 右、上 < 下。 */
function normalize([x0, y0, x1, y1]: Box): Box {
  const r = (v: number) => Math.round(v * 10) / 10 // 保留一位小数就够了
  return [r(Math.min(x0, x1)), r(Math.min(y0, y1)), r(Math.max(x0, x1)), r(Math.max(y0, y1))]
}

/**
 * 左边：页面原图，每个有出处的字段在上面画一个框。点框 = 选中右边对应的字段。
 * 显示原文文字块时，每块 OCR 认出的文字都有一个虚线框，点它 = 把这块的文字填进选中的字段。
 */
export function PageViewer({
  documentId,
  page,
  values,
  selectedId,
  hoveredId,
  onSelect,
  onHover,
  ocr,
  showOcr,
  onToggleOcr,
  picked,
  onPickLine,
  canDraw,
  drawOn,
  onToggleDraw,
  draft,
  onDraft,
  onDraftDone,
  extra,
}: Props) {
  const image = usePageImage(documentId, page)
  const [zoom, setZoom] = useState(1)
  // 图片的真实像素大小。出处坐标就是按这个算的，SVG 用它当坐标系
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const boxes = useRef(new Map<string, SVGRectElement>())
  const svg = useRef<SVGSVGElement>(null)
  // 正在拖的框：从哪里开始、拖的是哪个角（或整个框、或新画一个）、开始时框在哪
  const drag = useRef<{ handle: Handle; x: number; y: number; base: Box } | null>(null)
  const latest = useRef<Box | null>(null)

  /** 鼠标在屏幕上的位置 → 图片上的像素坐标（图片放大缩小了也对）。 */
  function toImage(e: { clientX: number; clientY: number }): { x: number; y: number } {
    const el = svg.current
    const matrix = el?.getScreenCTM()
    if (!el || !matrix) {
      return { x: 0, y: 0 }
    }
    const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(matrix.inverse())
    return { x: point.x, y: point.y }
  }

  function beginDrag(e: ReactPointerEvent, handle: Handle, base: Box | null) {
    e.stopPropagation()
    e.preventDefault()
    svg.current?.setPointerCapture(e.pointerId)
    const { x, y } = toImage(e)
    drag.current = { handle, x, y, base: base ?? [x, y, x, y] }
  }

  function moveDrag(e: ReactPointerEvent) {
    const d = drag.current
    if (!d) {
      return
    }
    const { x, y } = toImage(e)
    const dx = x - d.x
    const dy = y - d.y
    let [x0, y0, x1, y1] = d.base
    if (d.handle === 'new') [x0, y0, x1, y1] = [d.x, d.y, x, y]
    if (d.handle === 'move') [x0, y0, x1, y1] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
    if (d.handle === 'nw') [x0, y0] = [x0 + dx, y0 + dy]
    if (d.handle === 'ne') [x1, y0] = [x1 + dx, y0 + dy]
    if (d.handle === 'sw') [x0, y1] = [x0 + dx, y1 + dy]
    if (d.handle === 'se') [x1, y1] = [x1 + dx, y1 + dy]
    latest.current = normalize([x0, y0, x1, y1])
    onDraft(latest.current)
  }

  function endDrag(e: ReactPointerEvent) {
    if (!drag.current) {
      return
    }
    drag.current = null
    svg.current?.releasePointerCapture(e.pointerId)
    const box = latest.current
    latest.current = null
    if (box && box[2] - box[0] > 4 && box[3] - box[1] > 4) {
      onDraftDone(box)
    } else if (box) {
      onDraft(null) // 只是点了一下，不算画框
    }
  }
  const frame = useRef<HTMLDivElement>(null)

  // 右边选中一个字段时，把左边对应的框滚到看得见的地方
  useEffect(() => {
    if (selectedId) {
      boxes.current
        .get(selectedId)
        ?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    }
  }, [selectedId])

  const withBox = values.filter((v) => evidenceOf(v) && !v.deleted)
  // 要画的框：普通字段全画；表格（商品明细）的格子太多，只画选中和鼠标停着的那一格，不然满屏都是框。
  // 大框先画、小框后画：小框在上面，点得到
  const area = (v: FieldValue) => {
    const [x0, y0, x1, y1] = evidenceOf(v)!.bbox
    return (x1 - x0) * (y1 - y0)
  }
  const drawn = withBox
    .filter((v) => splitCell(v.name) === null || v.id === selectedId || v.id === hoveredId)
    .sort((a, b) => area(b) - area(a))
  /**
   * 原图上这一点是哪个字段的出处（包括商品明细里没画框的格子）：框住这一点的里面挑最小的。
   * 点原图 = 选中右边对应的字段（"点框反选"）。
   */
  function fieldAt(e: { clientX: number; clientY: number }): string | null {
    const { x, y } = toImage(e)
    const hits = withBox.filter((v) => {
      const [x0, y0, x1, y1] = evidenceOf(v)!.bbox
      return x >= x0 - PAD && x <= x1 + PAD && y >= y0 - PAD && y <= y1 + PAD
    })
    hits.sort((a, b) => area(a) - area(b))
    return hits[0]?.id ?? null
  }

  // 放大、缩小：跳到下一档。"整页"算出来的比例不在档位里，所以按大小找，不按位置找
  const zoomIn = () => setZoom(ZOOMS.find((z) => z > zoom + 0.001) ?? ZOOMS[ZOOMS.length - 1])
  const zoomOut = () => setZoom([...ZOOMS].reverse().find((z) => z < zoom - 0.001) ?? ZOOMS[0])

  // 框选时能拖的框：画好的框，或者选中字段现在的框
  const selectedValue = values.find((v) => v.id === selectedId)
  const editable: Box | null =
    draft ?? (selectedValue && evidenceOf(selectedValue) ? evidenceOf(selectedValue)!.bbox : null)

  // 整页：缩到整张图刚好放得下（细长的小票很需要）。zoom 是"图片宽 / 框宽"
  const fitPage = () => {
    const box = frame.current
    if (!box || !natural) {
      return
    }
    const width = box.clientWidth - FRAME_PADDING * 2
    const height = box.clientHeight - FRAME_PADDING * 2
    const fit = (height / width) * (natural.width / natural.height)
    setZoom(Math.min(1, fit))
  }

  return (
    <Flex vertical gap={8} style={{ height: '100%' }}>
      <Flex justify="space-between" align="center">
        <Typography.Text type="secondary">
          {drawOn
            ? '拖出新框，或拖动、拉伸橙色框'
            : showOcr
            ? '点文字块填入右边选中的字段；按住 Shift 或 ⌘/Ctrl 多选'
            : `${withBox.length} 个值找到了出处`}
        </Typography.Text>
        <Flex gap={4} align="center">
          {extra}
          <Tooltip
            title={
              ocr && !ocr.available
                ? '这一页处理时还没保存 OCR 结果，重新上传后才能用'
                : '显示 OCR 认出的每块文字（也可以按住 Alt / Option）。先在右边选中字段，再点文字填入；按住 Shift 或 ⌘/Ctrl 可以多选'
            }
          >
            <Button
              size="small"
              icon={<FontSizeOutlined />}
              type={showOcr ? 'primary' : 'default'}
              onClick={onToggleOcr}
              disabled={!ocr?.available}
            >
              原文文字
            </Button>
          </Tooltip>
          {canDraw && (
            <Tooltip title="在原图上拖出一个框，或者拖动、拉伸选中字段的框；然后用框里的文字，或者重新识别这块">
              <Button
                size="small"
                icon={<SelectOutlined />}
                type={drawOn ? 'primary' : 'default'}
                onClick={onToggleDraw}
              >
                框选
              </Button>
            </Tooltip>
          )}
          <Button size="small" icon={<ColumnHeightOutlined />} onClick={fitPage}>
            整页
          </Button>
          <Button
            size="small"
            icon={<ZoomOutOutlined />}
            onClick={zoomOut}
            disabled={zoom <= ZOOMS[0]}
          />
          <Button
            size="small"
            onClick={() => setZoom(1)}
            style={{ width: 64 }}
            title="点一下回到 100%（宽度占满）"
          >
            {Math.round(zoom * 100)}%
          </Button>
          <Button
            size="small"
            icon={<ZoomInOutlined />}
            onClick={zoomIn}
            disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
          />
        </Flex>
      </Flex>

      <div
        ref={frame}
        style={{
          flex: 1,
          overflow: 'auto',
          background: '#f0eef5',
          borderRadius: 8,
          padding: FRAME_PADDING,
        }}
      >
        {image.isPending && (
          <Flex justify="center" style={{ padding: 64 }}>
            <Spin />
          </Flex>
        )}
        {image.error && (
          <Alert
            type="error"
            showIcon
            title={image.error instanceof ApiError ? image.error.message : String(image.error)}
          />
        )}
        {image.data && (
          // 宽度按百分比缩放，SVG 盖在图片上、大小跟图片一样，框就跟着一起缩放
          <div style={{ position: 'relative', width: `${zoom * 100}%`, margin: '0 auto' }}>
            <img
              src={image.data}
              alt={`第 ${page} 页`}
              draggable={false}
              onLoad={(e) =>
                setNatural({
                  width: e.currentTarget.naturalWidth,
                  height: e.currentTarget.naturalHeight,
                })
              }
              style={{ display: 'block', width: '100%', boxShadow: '0 1px 4px rgba(0,0,0,0.15)' }}
            />
            {natural && (
              <svg
                ref={svg}
                viewBox={`0 0 ${natural.width} ${natural.height}`}
                preserveAspectRatio="none"
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '100%',
                  height: '100%',
                  cursor: drawOn ? 'crosshair' : undefined,
                  touchAction: drawOn ? 'none' : undefined,
                }}
                onPointerMove={moveDrag}
                onPointerUp={endDrag}
              >
                {drawOn && (
                  // 框选模式：在空白处按下就开始画新框
                  <rect
                    width={natural.width}
                    height={natural.height}
                    fill="transparent"
                    onPointerDown={(e) => beginDrag(e, 'new', null)}
                  />
                )}
                {!drawOn && !showOcr && (
                  // 平时：鼠标移到原文上，对应的字段（包括商品明细的格子）亮出框；点一下就选中它
                  <rect
                    width={natural.width}
                    height={natural.height}
                    fill="transparent"
                    onPointerMove={(e) => {
                      const id = fieldAt(e)
                      if (id !== hoveredId) onHover(id)
                    }}
                    onPointerLeave={() => onHover(null)}
                    onClick={(e) => {
                      const id = fieldAt(e)
                      if (id) onSelect(id)
                    }}
                    style={{ cursor: hoveredId ? 'pointer' : 'default' }}
                  />
                )}
                {drawn.map((v) => {
                  const [x0, y0, x1, y1] = evidenceOf(v)!.bbox
                  const active = v.id === selectedId || v.id === hoveredId
                  const color = boxColor(v)
                  return (
                    <rect
                      key={v.id}
                      ref={(el) => {
                        if (el) {
                          boxes.current.set(v.id, el)
                        } else {
                          boxes.current.delete(v.id)
                        }
                      }}
                      x={x0 - PAD}
                      y={y0 - PAD}
                      width={x1 - x0 + PAD * 2}
                      height={y1 - y0 + PAD * 2}
                      rx={4}
                      fill={color}
                      fillOpacity={active ? 0.25 : 0.08}
                      stroke={color}
                      strokeWidth={v.id === selectedId ? 3 : 2}
                      vectorEffect="non-scaling-stroke" // 放大缩小时线的粗细不变
                      // 显示原文文字块时，字段的框不挡着点文字
                      style={{
                        cursor: 'pointer',
                        pointerEvents: showOcr || drawOn ? 'none' : 'auto',
                      }}
                      onClick={() => onSelect(v.id)}
                      onMouseEnter={() => onHover(v.id)}
                      onMouseLeave={() => onHover(null)}
                    >
                      <title>{`${fieldLabel(v.name)}：${v.current_value ?? ''}`}</title>
                    </rect>
                  )
                })}
                {showOcr &&
                  !drawOn &&
                  ocr?.lines.map((line) => {
                    const [x0, y0, x1, y1] = line.box
                    const on = picked.includes(line.index)
                    return (
                      <rect
                        key={`ocr-${line.index}`}
                        x={x0 - 2}
                        y={y0 - 2}
                        width={x1 - x0 + 4}
                        height={y1 - y0 + 4}
                        rx={2}
                        fill="#1677ff"
                        fillOpacity={on ? 0.3 : 0.04}
                        stroke="#1677ff"
                        strokeWidth={on ? 2 : 1}
                        strokeDasharray={on ? undefined : '4 3'}
                        vectorEffect="non-scaling-stroke"
                        style={{ cursor: 'copy' }}
                        // 按着 Shift 点的时候，浏览器默认会去选中页面上的文字，这里不要
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={(e) => onPickLine(line.index, e.shiftKey || e.metaKey || e.ctrlKey)}
                      >
                        <title>{`${line.text}（OCR 置信度 ${Math.round(line.confidence * 100)}%）`}</title>
                      </rect>
                    )
                  })}
                {drawOn && editable && (
                  <g>
                    <rect
                      x={editable[0]}
                      y={editable[1]}
                      width={editable[2] - editable[0]}
                      height={editable[3] - editable[1]}
                      fill="#fa8c16"
                      fillOpacity={0.12}
                      stroke="#fa8c16"
                      strokeWidth={2}
                      strokeDasharray="6 4"
                      vectorEffect="non-scaling-stroke"
                      style={{ cursor: 'move' }}
                      onPointerDown={(e) => beginDrag(e, 'move', editable)}
                    />
                  </g>
                )}
              </svg>
            )}
            {natural &&
              drawOn &&
              editable &&
              // 拉伸用的四个小方块用 HTML 画：屏幕上一直是 10 像素大，放大到 300% 也不会变成大方块
              (['nw', 'ne', 'sw', 'se'] as const).map((corner) => (
                <div
                  key={corner}
                  onPointerDown={(e) => beginDrag(e, corner, editable)}
                  style={{
                    position: 'absolute',
                    left: `${((corner.endsWith('w') ? editable[0] : editable[2]) / natural.width) * 100}%`,
                    top: `${((corner.startsWith('n') ? editable[1] : editable[3]) / natural.height) * 100}%`,
                    width: 10,
                    height: 10,
                    transform: 'translate(-50%, -50%)',
                    background: '#fff',
                    border: '2px solid #fa8c16',
                    borderRadius: 2,
                    touchAction: 'none',
                    cursor: corner === 'nw' || corner === 'se' ? 'nwse-resize' : 'nesw-resize',
                  }}
                />
              ))}
          </div>
        )}
      </div>
    </Flex>
  )
}
