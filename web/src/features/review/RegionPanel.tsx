import { ScanOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Flex, Input, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { ocrRegion } from '../../api/documents'
import type { Box, OcrLine } from '../../api/types'

interface Props {
  documentId: string
  page: number
  box: Box
  /** 要填的字段的名字，例如"第 2 行 商品"。 */
  label: string
  /** 这一页的 OCR 文字块（没保存 OCR 结果的老文档是空的）。 */
  lines: OcrLine[]
  busy: boolean
  /** 用框里已经认出的文字块（和"点原文填值"一样）。 */
  onUseLines: (indexes: number[]) => void
  /** 用重新识别、人确认过的文字。 */
  onUseText: (text: string) => void
  onCancel: () => void
}

/** 框里的文字块：一半以上落在框里的就算（框画得比文字小一点也能选上）。 */
function linesIn(lines: OcrLine[], [x0, y0, x1, y1]: Box): OcrLine[] {
  return lines.filter((l) => {
    const [a0, b0, a1, b1] = l.box
    const w = Math.min(a1, x1) - Math.max(a0, x0)
    const h = Math.min(b1, y1) - Math.max(b0, y0)
    const area = (a1 - a0) * (b1 - b0)
    return w > 0 && h > 0 && area > 0 && (w * h) / area >= 0.5
  })
}

/**
 * 框好之后：两种用法。
 * - 用框里的文字：框住的 OCR 文字块直接填进去（快）；
 * - 重新识别这块：把这块裁下来放大再认一次，结果可以改，确认后填进去（OCR 认错的地方用这个）。
 */
export function RegionPanel({
  documentId,
  page,
  box,
  label,
  lines,
  busy,
  onUseLines,
  onUseText,
  onCancel,
}: Props) {
  const inside = linesIn(lines, box)
  const [text, setText] = useState<string | null>(null)
  const read = useMutation({
    mutationFn: () => ocrRegion(documentId, page, box),
    onSuccess: (result) => setText(result.text),
  })

  return (
    <Alert
      type="warning"
      style={{ marginBottom: 8 }}
      title={`框选，填入「${label}」`}
      description={
        <Flex vertical gap={8} style={{ marginTop: 4 }}>
          <Flex gap={8} align="center" wrap>
            <Typography.Text style={{ flex: 1, minWidth: 160 }}>
              框里的文字：
              {inside.length > 0 ? (
                `「${inside.map((l) => l.text).join(' ')}」`
              ) : (
                <Typography.Text type="secondary">（没有认出的文字）</Typography.Text>
              )}
            </Typography.Text>
            <Button
              size="small"
              type="primary"
              disabled={inside.length === 0}
              loading={busy && text === null}
              onClick={() => onUseLines(inside.map((l) => l.index))}
            >
              用框里的文字
            </Button>
            <Button
              size="small"
              icon={<ScanOutlined />}
              loading={read.isPending}
              onClick={() => read.mutate()}
            >
              重新识别这块
            </Button>
            <Button size="small" onClick={onCancel}>
              取消（Esc）
            </Button>
          </Flex>
          {read.error && (
            <Typography.Text type="danger">
              {read.error instanceof ApiError ? read.error.message : String(read.error)}
            </Typography.Text>
          )}
          {text !== null && (
            <Flex gap={8} align="center">
              <Typography.Text>重新识别：</Typography.Text>
              <Input
                size="small"
                autoFocus
                value={text}
                placeholder="没认出文字，可以直接输入"
                onChange={(e) => setText(e.target.value)}
                onPressEnter={() => text.trim() && onUseText(text.trim())}
                style={{ flex: 1 }}
              />
              <Button
                size="small"
                type="primary"
                disabled={!text.trim()}
                loading={busy}
                onClick={() => onUseText(text.trim())}
              >
                填入
              </Button>
            </Flex>
          )}
        </Flex>
      }
    />
  )
}
