import { EyeOutlined, ScanOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Flex, Input, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { lookRegion, ocrRegion } from '../../api/documents'
import { getVision } from '../../api/system'
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
  /** 字段类型（string、number、date），让模型按类型写值。 */
  type?: string
  /**
   * 用重新识别（或模型看图读出）、人确认过的文字。byModel：是模型看图读的。
   * transcription：图中原文（开了"自动收集纠错样本"才有，ADR 0050）。
   */
  onUseText: (text: string, byModel: boolean, transcription?: string) => void
  /** 开了"自动收集纠错样本"：填入的同时把框里的图和图中原文存成 OCR 样本。 */
  collecting?: boolean
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
 * 框好之后：三种用法。
 * - 用框里的文字：框住的 OCR 文字块直接填进去（快）；
 * - 重新识别这块：把这块裁下来放大再认一次，结果可以改，确认后填进去（OCR 认错的地方用这个）；
 * - 让模型看这块（ADR 0038，要有视觉模型）：手写、印章、被笔迹盖住的字，模型看图读出要填的值。
 */
export function RegionPanel({
  documentId,
  page,
  box,
  label,
  lines,
  busy,
  type,
  onUseLines,
  onUseText,
  collecting = false,
  onCancel,
}: Props) {
  const inside = linesIn(lines, box)
  const [text, setText] = useState<string | null>(null)
  // 图中原文单独填的（框里还有别的字，和要填的值不一样）；null = 和填入的值一样
  const [own, setOwn] = useState<string | null>(null)
  const [byModel, setByModel] = useState(false)
  const [seen, setSeen] = useState<string | null>(null)
  const vision = useQuery({ queryKey: ['vision'], queryFn: getVision, staleTime: 60_000 })
  const read = useMutation({
    mutationFn: () => ocrRegion(documentId, page, box),
    onSuccess: (result) => {
      setText(result.text)
      setByModel(false)
      setSeen(null)
    },
  })
  const look = useMutation({
    mutationFn: () => lookRegion(documentId, page, { box, label, type }),
    onSuccess: (result) => {
      setText(result.value ?? result.text)
      setByModel(true)
      setSeen(result.text)
    },
  })
  const error = read.error ?? look.error
  // 模型看图读的值可能已经转过格式，图中原文默认用它在图上看到的字
  const transcription = collecting ? (own ?? (byModel && seen ? seen : text) ?? undefined) : undefined
  const use = () => {
    if (text?.trim()) onUseText(text.trim(), byModel, transcription?.trim() || undefined)
  }

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
            {vision.data?.sees_images && (
              <Button
                size="small"
                icon={<EyeOutlined />}
                loading={look.isPending}
                onClick={() => look.mutate()}
              >
                让模型看这块
              </Button>
            )}
            <Button size="small" onClick={onCancel}>
              取消（Esc）
            </Button>
          </Flex>
          {error && (
            <Typography.Text type="danger">
              {error instanceof ApiError ? error.message : String(error)}
            </Typography.Text>
          )}
          {byModel && seen !== null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              模型在图上看到：「{seen || '（没看出字）'}」。手写、盖住的字模型也可能看错，填之前对一下原图。
            </Typography.Text>
          )}
          {text !== null && (
            <Flex gap={8} align="center">
              <Typography.Text>{byModel ? '模型读的：' : '重新识别：'}</Typography.Text>
              <Input
                size="small"
                autoFocus
                value={text}
                placeholder="没认出文字，可以直接输入"
                onChange={(e) => setText(e.target.value)}
                onPressEnter={use}
                style={{ flex: 1 }}
              />
              <Button
                size="small"
                type="primary"
                disabled={!text.trim()}
                loading={busy}
                onClick={use}
              >
                填入
              </Button>
            </Flex>
          )}
          {text !== null && collecting && own === null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              图中原文{byModel && seen ? `用模型在图上看到的「${seen}」` : '和上面填入的一样'}
              ，会和框里的图一起存成 OCR 样本。框里还有别的字（比如标签）？
              <Button type="link" size="small" onClick={() => setOwn(transcription ?? text)}>
                单独填写图中原文
              </Button>
            </Typography.Text>
          )}
          {text !== null && collecting && own !== null && (
            <Flex gap={8} align="center">
              <Typography.Text>图中原文：</Typography.Text>
              <Input
                size="small"
                value={own}
                placeholder="框里所有的字，按图上原样"
                onChange={(e) => setOwn(e.target.value)}
                onPressEnter={use}
                style={{ flex: 1, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}
              />
            </Flex>
          )}
        </Flex>
      }
    />
  )
}
