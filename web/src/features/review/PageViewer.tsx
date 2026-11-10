import { ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons'
import { Alert, Button, Flex, Spin, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import type { FieldValue } from '../../api/types'
import { boxColor, fieldLabel } from './fields'
import { usePageImage } from './usePageImage'

const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3]
const PAD = 4 // 框比文字稍大一点，不压住字

interface Props {
  documentId: string
  page: number
  values: FieldValue[]
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}

/** 左边：页面原图，每个有出处的字段在上面画一个框。点框 = 选中右边对应的字段。 */
export function PageViewer({
  documentId,
  page,
  values,
  selectedId,
  hoveredId,
  onSelect,
  onHover,
}: Props) {
  const image = usePageImage(documentId, page)
  const [zoom, setZoom] = useState(1)
  // 图片的真实像素大小。出处坐标就是按这个算的，SVG 用它当坐标系
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)
  const boxes = useRef(new Map<string, SVGRectElement>())

  // 右边选中一个字段时，把左边对应的框滚到看得见的地方
  useEffect(() => {
    if (selectedId) {
      boxes.current
        .get(selectedId)
        ?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
    }
  }, [selectedId])

  const withBox = values.filter((v) => v.evidence)
  const step = (delta: number) => {
    const next = ZOOMS.indexOf(zoom) + delta
    setZoom(ZOOMS[Math.min(Math.max(next, 0), ZOOMS.length - 1)])
  }

  return (
    <Flex vertical gap={8} style={{ height: '100%' }}>
      <Flex justify="space-between" align="center">
        <Typography.Text type="secondary">
          {withBox.length} 个字段在原图上找到了出处，点框或右边的字段可以对照
        </Typography.Text>
        <Flex gap={4} align="center">
          <Button
            size="small"
            icon={<ZoomOutOutlined />}
            onClick={() => step(-1)}
            disabled={zoom === ZOOMS[0]}
          />
          <Button size="small" onClick={() => setZoom(1)} style={{ width: 64 }}>
            {Math.round(zoom * 100)}%
          </Button>
          <Button
            size="small"
            icon={<ZoomInOutlined />}
            onClick={() => step(1)}
            disabled={zoom === ZOOMS[ZOOMS.length - 1]}
          />
        </Flex>
      </Flex>

      <div
        style={{
          flex: 1,
          overflow: 'auto',
          background: '#f0eef5',
          borderRadius: 8,
          padding: 16,
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
                viewBox={`0 0 ${natural.width} ${natural.height}`}
                preserveAspectRatio="none"
                style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
              >
                {withBox.map((v) => {
                  const [x0, y0, x1, y1] = v.evidence!.bbox
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
                      style={{ cursor: 'pointer' }}
                      onClick={() => onSelect(v.id)}
                      onMouseEnter={() => onHover(v.id)}
                      onMouseLeave={() => onHover(null)}
                    >
                      <title>{`${fieldLabel(v.name)}：${v.current_value ?? ''}`}</title>
                    </rect>
                  )
                })}
              </svg>
            )}
          </div>
        )}
      </div>
    </Flex>
  )
}
