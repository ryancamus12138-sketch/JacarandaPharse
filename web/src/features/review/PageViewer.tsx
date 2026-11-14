import {
  ApartmentOutlined,
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
import type { Box, FieldValue, PageOcr, PageStructure } from '../../api/types'
import { boxColor, evidenceOf, fieldLabel, shownValue, splitCell } from './fields'
import { usePageImage } from './usePageImage'
import { useTranslation } from 'react-i18next'

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3]
const PAD = 4 // Boxes slightly larger than the text so they do not cover it.
const FRAME_PADDING = 16

interface Props {
  documentId: string
  page: number
  values: FieldValue[]
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** OCR result of this page (for "select from source text"). undefined until loaded. */
  ocr: PageOcr | undefined
  /** Show source text blocks: hold Alt, or toggle "Source text" in the toolbar. */
  showOcr: boolean
  onToggleOcr: () => void
  /** Text blocks being multi-selected (clicked with Shift or ⌘/Ctrl). */
  picked: number[]
  /** A text block was clicked. multi: Shift or ⌘/Ctrl was held (multi-select). */
  onPickLine: (index: number, multi: boolean) => void
  /** Whether regions can be selected (requires the edit-fields permission). */
  canDraw: boolean
  /** Region mode: drag a box on the image, or move and resize the selected field's box. */
  drawOn: boolean
  onToggleDraw: () => void
  /** Box being drawn, or drawn but not yet used. */
  draft: Box | null
  /** The box changes continuously while dragging; null cancels. */
  onDraft: (box: Box | null) => void
  /** Mouse released: the box is complete. */
  onDraftDone: (box: Box) => void
  /** Extra buttons at the start of the toolbar (for example "Confirm"). */
  extra?: ReactNode
  /** Structure of this page (layout regions, tables, label → value pairs). undefined until loaded. */
  structure?: PageStructure
  /** Show the layout structure: regions, table cells, labels and values detected by the system. */
  showStructure?: boolean
  onToggleStructure?: () => void
}

type Handle = 'new' | 'move' | 'nw' | 'ne' | 'sw' | 'se'

/** Normalize a box so that left < right and top < bottom. */
function normalize([x0, y0, x1, y1]: Box): Box {
  const r = (v: number) => Math.round(v * 10) / 10 // One decimal place is enough.
  return [r(Math.min(x0, x1)), r(Math.min(y0, y1)), r(Math.max(x0, x1)), r(Math.max(y0, y1))]
}

/**
 * Left: the page image with a box for every field that has evidence. Clicking a box selects the corresponding
 * field on the right.
 * When source text blocks are shown, each OCR block has a dashed box; clicking one fills its text into the
 * selected field.
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
  structure,
  showStructure = false,
  onToggleStructure,
}: Props) {
  const { t } = useTranslation()
  const image = usePageImage(documentId, page)
  const [zoom, setZoom] = useState(1)
  // Natural pixel size of the image. Evidence coordinates refer to it, and the SVG uses it as its coordinate
  // system.
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const boxes = useRef(new Map<string, SVGRectElement>())
  const svg = useRef<SVGSVGElement>(null)
  // Box being dragged: start point, the handle being dragged (a corner, the whole box, or a new box) and the
  // box at the start.
  const drag = useRef<{ handle: Handle; x: number; y: number; base: Box } | null>(null)
  const latest = useRef<Box | null>(null)

  /** Screen position of the pointer → pixel coordinates on the image (correct at any zoom). */
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
      onDraft(null) // A simple click, not a drawn box.
    }
  }
  const frame = useRef<HTMLDivElement>(null)

  // When a field is selected on the right, scroll its box on the left into view.
  useEffect(() => {
    if (selectedId) {
      boxes.current
        .get(selectedId)
        ?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    }
  }, [selectedId])

  const withBox = values.filter((v) => evidenceOf(v) && !v.deleted)
  // Boxes to draw: all regular fields; table cells are too many, so only the selected and hovered cell, to
  // avoid filling the screen with boxes.
  // Large boxes first, small boxes last: small boxes stay on top and clickable.
  const area = (v: FieldValue) => {
    const [x0, y0, x1, y1] = evidenceOf(v)!.bbox
    return (x1 - x0) * (y1 - y0)
  }
  const drawn = withBox
    .filter((v) => splitCell(v.name) === null || v.id === selectedId || v.id === hoveredId)
    .sort((a, b) => area(b) - area(a))
  /**
   * The field whose evidence contains this point on the image (including line-item cells without drawn
   * boxes): the smallest box containing it. Clicking the image selects the corresponding field on the right.
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

  // Zoom in and out by steps. The computed "fit page" ratio is not a step, so the next step is found by
  // value, not index.
  const zoomIn = () => setZoom(ZOOMS.find((z) => z > zoom + 0.001) ?? ZOOMS[ZOOMS.length - 1])
  const zoomOut = () => setZoom([...ZOOMS].reverse().find((z) => z < zoom - 0.001) ?? ZOOMS[0])

  // Draggable box in region mode: the drawn box, or the selected field's current box.
  const selectedValue = values.find((v) => v.id === selectedId)
  const editable: Box | null =
    draft ?? (selectedValue && evidenceOf(selectedValue) ? evidenceOf(selectedValue)!.bbox : null)

  // Fit page: scale so the whole image fits (essential for long narrow receipts). zoom is image width /
  // container width.
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
      <Flex justify="space-between" align="center" wrap gap={8}>
        <Typography.Text type="secondary" style={{ flex: '1 1 200px', minWidth: 0 }}>
          {drawOn
            ? t('拖动绘制新框，或移动、缩放橙色框')
            : showOcr
            ? t('点击文字块填入右侧选中的字段；按住 Shift 或 ⌘/Ctrl 可多选')
            : t('{{length}} 个值已找到出处', { length: withBox.length })}
        </Typography.Text>
        <Flex gap={4} align="center" wrap>
          {extra}
          <Tooltip
            title={
              ocr && !ocr.available
                ? t('此页处理时未保存 OCR 结果，需重新上传后方可使用')
                : t('显示 OCR 识别的各文字块（也可按住 Alt / Option）。先在右侧选中字段，再点击文字填入；按住 Shift 或 ⌘/Ctrl 可多选')
            }
          >
            <Button
              size="small"
              icon={<FontSizeOutlined />}
              type={showOcr ? 'primary' : 'default'}
              onClick={onToggleOcr}
              disabled={!ocr?.available}
            >
              {t('原文文字')}
            </Button>
          </Tooltip>
          {onToggleStructure && (
            <Tooltip
              title={
                structure && !structure.available
                  ? t('此页在更新前处理，无版面结构；重新处理后可用')
                  : t('显示系统识别的结构：版面区域（虚线）、表格单元格（紫色）、标题（橙色）→ 值（绿色）')
              }
            >
              <Button
                size="small"
                icon={<ApartmentOutlined />}
                type={showStructure ? 'primary' : 'default'}
                onClick={onToggleStructure}
                disabled={!structure?.available}
              >
                {t('版面结构')}
              </Button>
            </Tooltip>
          )}
          {canDraw && (
            <Tooltip title={t('在原图上拖动绘制框，或移动、缩放所选字段的框；然后使用框内文字或重新识别该区域')}>
              <Button
                size="small"
                icon={<SelectOutlined />}
                type={drawOn ? 'primary' : 'default'}
                onClick={onToggleDraw}
              >
                {t('框选')}
              </Button>
            </Tooltip>
          )}
          <Button size="small" icon={<ColumnHeightOutlined />} onClick={fitPage}>
            {t('整页')}
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
            title={t('点击恢复为 100%（适应宽度）')}
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
          // Width scales by percentage and the SVG overlays the image at the same size, so boxes scale with
          // it.
          <div style={{ position: 'relative', width: `${zoom * 100}%`, margin: '0 auto' }}>
            <img
              src={image.data}
              alt={t('第 {{page}} 页', { page })}
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
                  // Region mode: pressing on empty space starts a new box.
                  <rect
                    width={natural.width}
                    height={natural.height}
                    fill="transparent"
                    onPointerDown={(e) => beginDrag(e, 'new', null)}
                  />
                )}
                {!drawOn && !showOcr && (
                  // Default: hovering over source text highlights the box of the corresponding field
                  // (including line-item cells); clicking selects it.
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
                      vectorEffect="non-scaling-stroke" // Stroke width stays constant when zooming.
                      // While source text blocks are shown, field boxes do not block clicks on the text.
                      style={{
                        cursor: 'pointer',
                        pointerEvents: showOcr || drawOn ? 'none' : 'auto',
                      }}
                      onClick={() => onSelect(v.id)}
                      onMouseEnter={() => onHover(v.id)}
                      onMouseLeave={() => onHover(null)}
                    >
                      <title>{t('{{fieldLabel}}：{{value}}', { fieldLabel: fieldLabel(v.name), value: shownValue(v) ?? '' })}</title>
                    </rect>
                  )
                })}
                {showStructure && structure?.available && (
                  // Display only, not clickable: clicks pass through to the field boxes below.
                  <g style={{ pointerEvents: 'none' }}>
                    {structure.regions.map((r, i) => (
                      <g key={`region-${i}`}>
                        <rect
                          x={r.box[0]}
                          y={r.box[1]}
                          width={r.box[2] - r.box[0]}
                          height={r.box[3] - r.box[1]}
                          fill="none"
                          stroke="#8c8c8c"
                          strokeWidth={1}
                          strokeDasharray="6 4"
                          vectorEffect="non-scaling-stroke"
                        />
                        <text x={r.box[0] + 4} y={r.box[1] - 4} fontSize={16} fill="#595959">
                          {r.kind}
                        </text>
                      </g>
                    ))}
                    {structure.tables.flatMap((item, ti) =>
                      item.cells.map((c) => (
                        <rect
                          key={`cell-${ti}-${c.row}-${c.col}`}
                          x={c.box[0] - 2}
                          y={c.box[1] - 2}
                          width={c.box[2] - c.box[0] + 4}
                          height={c.box[3] - c.box[1] + 4}
                          fill="#722ed1"
                          fillOpacity={0.06}
                          stroke="#722ed1"
                          strokeWidth={1}
                          vectorEffect="non-scaling-stroke"
                        >
                          <title>{t('第 {{row}} 行第 {{col}} 列：{{text}}', { row: c.row, col: c.col, text: c.text })}</title>
                        </rect>
                      )),
                    )}
                    {structure.pairs.map((pair, i) => {
                      const [k0, k1, k2, k3] = pair.key_box
                      const [v0, v1, v2, v3] = pair.value_box
                      const same = k0 === v0 && k1 === v1 && k2 === v2 && k3 === v3
                      return (
                        <g key={`pair-${i}`}>
                          {!same && (
                            <line
                              x1={(k0 + k2) / 2}
                              y1={(k1 + k3) / 2}
                              x2={(v0 + v2) / 2}
                              y2={(v1 + v3) / 2}
                              stroke="#fa8c16"
                              strokeWidth={1.5}
                              vectorEffect="non-scaling-stroke"
                            />
                          )}
                          <rect
                            x={k0 - 3}
                            y={k1 - 3}
                            width={k2 - k0 + 6}
                            height={k3 - k1 + 6}
                            fill="none"
                            stroke="#fa8c16"
                            strokeWidth={1.5}
                            vectorEffect="non-scaling-stroke"
                          />
                          {!same && (
                            <rect
                              x={v0 - 3}
                              y={v1 - 3}
                              width={v2 - v0 + 6}
                              height={v3 - v1 + 6}
                              fill="#52c41a"
                              fillOpacity={0.08}
                              stroke="#52c41a"
                              strokeWidth={1.5}
                              vectorEffect="non-scaling-stroke"
                            />
                          )}
                        </g>
                      )
                    })}
                  </g>
                )}
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
                        // Shift-click would make the browser select page text by default; prevent that.
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={(e) => onPickLine(line.index, e.shiftKey || e.metaKey || e.ctrlKey)}
                      >
                        <title>{t('{{text}}（OCR 置信度 {{round}}%）', { text: line.text, round: Math.round(line.confidence * 100) })}</title>
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
              // The four resize handles are HTML elements so they stay 10 pixels on screen even at 300% zoom.
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
