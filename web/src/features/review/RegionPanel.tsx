import { EyeOutlined, ScanOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Button, Flex, Input, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { lookRegion, ocrRegion } from '../../api/documents'
import { getVision } from '../../api/system'
import type { Box, OcrLine } from '../../api/types'
import { useTranslation } from 'react-i18next'

interface Props {
  documentId: string
  page: number
  box: Box
  /** Name of the target field, for example "Row 2 Item". */
  label: string
  /** OCR text blocks of this page (empty for older documents without stored OCR results). */
  lines: OcrLine[]
  busy: boolean
  /** Use the text blocks already recognized inside the box (like click-to-fill). */
  onUseLines: (indexes: number[]) => void
  /** Field type (string, number, date), so the model writes the value accordingly. */
  type?: string
  /**
   * Use the text from re-recognition (or read by the model from the image), confirmed by the reviewer.
   * byModel: read by the model.
   * transcription: transcription (only with OCR sample collection enabled).
   */
  onUseText: (text: string, byModel: boolean, transcription?: string) => void
  /**
   * With OCR sample collection enabled, filling the value also stores the region image and transcription as
   * an OCR sample.
   */
  collecting?: boolean
  onCancel: () => void
}

/**
 * Text blocks inside the box: those with more than half their area inside (so slightly smaller boxes still
 * select them).
 */
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
 * Once a region is drawn, three options:
 * - Use the text in the box: fill in the OCR text blocks inside the box (fast);
 * - Re-recognize the region: crop, enlarge and recognize again; the result is editable and filled in after
 *   confirmation (for OCR misreads);
 * - Ask the model about the region (requires a vision projector): for handwriting, stamps and text covered by
 *   pen marks, the model reads the target value from the image.
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
  const { t } = useTranslation()
  const inside = linesIn(lines, box)
  const [text, setText] = useState<string | null>(null)
  // Transcription entered separately (the box contains other text and differs from the value); null = same as
  // the filled value.
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
  // Values read by the model may already be reformatted; the transcription defaults to the text the model saw
  // on the image.
  const transcription = collecting ? (own ?? (byModel && seen ? seen : text) ?? undefined) : undefined
  const use = () => {
    if (text?.trim()) onUseText(text.trim(), byModel, transcription?.trim() || undefined)
  }

  return (
    <Alert
      type="warning"
      style={{ marginBottom: 8 }}
      title={t('框选，填入「{{label}}」', { label })}
      description={
        <Flex vertical gap={8} style={{ marginTop: 4 }}>
          <Flex gap={8} align="center" wrap>
            <Typography.Text style={{ flex: 1, minWidth: 160 }}>
              {t('框里的文字：')}
              {inside.length > 0 ? (
                t('「{{join}}」', { join: inside.map((l) => l.text).join(' ') })
              ) : (
                <Typography.Text type="secondary">{t('（没有认出的文字）')}</Typography.Text>
              )}
            </Typography.Text>
            <Button
              size="small"
              type="primary"
              disabled={inside.length === 0}
              loading={busy && text === null}
              onClick={() => onUseLines(inside.map((l) => l.index))}
            >
              {t('用框里的文字')}
            </Button>
            <Button
              size="small"
              icon={<ScanOutlined />}
              loading={read.isPending}
              onClick={() => read.mutate()}
            >
              {t('重新识别这块')}
            </Button>
            {vision.data?.sees_images && (
              <Button
                size="small"
                icon={<EyeOutlined />}
                loading={look.isPending}
                onClick={() => look.mutate()}
              >
                {t('让模型看这块')}
              </Button>
            )}
            <Button size="small" onClick={onCancel}>
              {t('取消（Esc）')}
            </Button>
          </Flex>
          {error && (
            <Typography.Text type="danger">
              {error instanceof ApiError ? error.message : String(error)}
            </Typography.Text>
          )}
          {byModel && seen !== null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('模型在图上看到：「{{seen}}」。手写、盖住的字模型也可能看错，填之前对一下原图。', { seen: seen || t('（没看出字）') })}
            </Typography.Text>
          )}
          {text !== null && (
            <Flex gap={8} align="center">
              <Typography.Text>{byModel ? t('模型读的：') : t('重新识别：')}</Typography.Text>
              <Input
                size="small"
                autoFocus
                value={text}
                placeholder={t('没认出文字，可以直接输入')}
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
                {t('填入')}
              </Button>
            </Flex>
          )}
          {text !== null && collecting && own === null && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('图中原文')}{byModel && seen ? t('用模型在图上看到的「{{seen}}」', { seen }) : t('和上面填入的一样')}
              {t('，会和框里的图一起存成 OCR 样本。框里还有别的字（比如标签）？')}
              <Button type="link" size="small" onClick={() => setOwn(transcription ?? text)}>
                {t('单独填写图中原文')}
              </Button>
            </Typography.Text>
          )}
          {text !== null && collecting && own !== null && (
            <Flex gap={8} align="center">
              <Typography.Text>{t('图中原文：')}</Typography.Text>
              <Input
                size="small"
                value={own}
                placeholder={t('框里所有的字，按图上原样')}
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
