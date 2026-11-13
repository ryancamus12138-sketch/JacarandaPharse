import { Alert, Button, Flex, Input, Typography } from 'antd'
import { useState } from 'react'

interface Props {
  /** 这块原文 OCR 读的文字。 */
  ocrText: string
  /** 要填的字段的名字。 */
  label: string
  busy: boolean
  /** 用改好的图中原文填入（字段值从它读出），同时存成 OCR 样本。 */
  onUse: (transcription: string) => void
  onCancel: () => void
}

/**
 * 核对图中原文（ADR 0050，开了"自动收集纠错样本"才有）：点一块原文后先在这里按图改对，
 * 回车填入。字段值从改好的文字读出（日期、数字照旧转成标准格式），这块原文的小图和这段文字存成一条 OCR 样本。
 */
export function TranscriptionPanel({ ocrText, label, busy, onUse, onCancel }: Props) {
  const [text, setText] = useState(ocrText)
  const ready = text.trim() !== ''
  const changed = text.trim() !== ocrText
  return (
    <Alert
      type="info"
      style={{ marginBottom: 8 }}
      title={`核对图中原文，填入「${label}」`}
      description={
        <Flex vertical gap={6} style={{ marginTop: 4 }}>
          <Flex gap={8} align="center">
            <Input
              size="small"
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              onPressEnter={() => ready && onUse(text)}
              onKeyDown={(e) => e.key === 'Escape' && onCancel()}
              style={{ flex: 1, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}
            />
            <Button size="small" type="primary" disabled={!ready} loading={busy} onClick={() => onUse(text)}>
              填入（Enter）
            </Button>
            <Button size="small" onClick={onCancel}>
              取消（Esc）
            </Button>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {changed
              ? `OCR 读的是「${ocrText}」。保存后存成一条「认错字」样本。`
              : '和图上一样就直接回车；有认错的字，按图上原样改（空格、大小写、标点、前导零都照抄）。'}
          </Typography.Text>
        </Flex>
      }
    />
  )
}
