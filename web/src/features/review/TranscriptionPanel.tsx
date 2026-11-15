import { Alert, Button, Flex, Input, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface Props {
  /** Text OCR read from this source block. */
  ocrText: string
  /** Name of the target field. */
  label: string
  busy: boolean
  /**
   * Fill in from the corrected transcription (the field value is parsed from it) and store it as an OCR
   * sample.
   */
  onUse: (transcription: string) => void
  onCancel: () => void
}

/**
 * Check the transcription (only with OCR sample collection enabled): after clicking a source block, correct
 * it here against the image and press Enter to fill in. The field value is parsed from the corrected text
 * (dates and numbers normalized as usual), and the block's crop and this text are stored as an OCR sample.
 */
export function TranscriptionPanel({ ocrText, label, busy, onUse, onCancel }: Props) {
  const { t } = useTranslation()
  const [text, setText] = useState(ocrText)
  const ready = text.trim() !== ''
  const changed = text.trim() !== ocrText
  return (
    <Alert
      type="info"
      style={{ marginBottom: 8 }}
      title={t('核对图中原文，填入「{{label}}」', { label })}
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
              {t('填入（Enter）')}
            </Button>
            <Button size="small" onClick={onCancel}>
              {t('取消（Esc）')}
            </Button>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {changed
              ? t('OCR 识别结果为「{{ocrText}}」。保存后将记录为一条「识别错误」样本。', { ocrText })
              : t('与图像一致时直接按回车；存在识别错误时，请按图像内容逐字修改（空格、大小写、标点和前导零均需照录）。')}
          </Typography.Text>
        </Flex>
      }
    />
  )
}
