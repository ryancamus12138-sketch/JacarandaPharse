import { BulbOutlined, EyeOutlined, FileTextOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Flex, Popover, Spin, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { getSentText } from '../../api/documents'
import type { Extraction } from '../../api/types'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const BY_TEXT = { get model() { return t('模型选的') }, get keywords() { return t('按字段名、模板名对出来的') }, get none() { return t('没有可选的模板') } }

/**
 * How this document was processed: which template automatic selection picked and why, and whether the model
 * was shown the page image.
 * Renders nothing when neither applies.
 */
export function ExtractionNotes({
  extraction,
  documentId,
}: {
  extraction: Extraction | undefined
  documentId: string
}) {
  const { t } = useTranslation()
  const classified = extraction?.classified
  const vision = extraction?.vision
  // Learning from corrections: the prompt version is recorded as extract-v8+lessons3.
  const lessons = Number(/\+(?:lessons|curated)(\d+)/.exec(extraction?.prompt_version ?? '')?.[1] ?? 0)
  const sent = extraction?.sent
  if (!classified && !vision && !lessons && !sent) return null
  return (
    <Flex gap={8} wrap align="center" style={{ marginBottom: 12 }}>
      {classified && (
        <Popover
          title={t('自动判断用哪个模板')}
          content={
            <Flex vertical gap={6} style={{ maxWidth: 360 }}>
              <Typography.Text>{classified.reason}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {BY_TEXT[classified.by]}
              </Typography.Text>
              {classified.candidates.length > 0 && (
                <>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t('比较过的模板（按字段名对上的多少排）：')}
                  </Typography.Text>
                  {classified.candidates.map((c) => (
                    <Flex key={c.template_id} justify="space-between" gap={12}>
                      <Typography.Text
                        strong={c.template_id === classified.picked?.template_id}
                        ellipsis
                      >
                        {c.title}
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        {Math.round(Math.min(c.score, 1) * 100)}%
                      </Typography.Text>
                    </Flex>
                  ))}
                </>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t('选得不对：点"重新处理"，换一个模板。')}
              </Typography.Text>
            </Flex>
          }
        >
          <Tag icon={<ThunderboltOutlined />} color="purple" style={{ cursor: 'pointer' }}>
            {t('自动判断：')}{classified.picked ? classified.picked.title : t('没有合适的模板')}
          </Tag>
        </Popover>
      )}
      {vision && (
        <Tooltip title={t('{{vision}}。OCR 文字里对不上的值标了"看原图读的"，请对着原图核对。', { vision })}>
          <Tag icon={<EyeOutlined />} color="orange">
            {t('模型看了原图')}
          </Tag>
        </Tooltip>
      )}
      {lessons > 0 && (
        <Tooltip
          title={t('抽取时告诉了模型：同一个模板以前人工改过的 {{lessons}} 个地方（容易错在哪），值仍然按这张单据的原文。在 .env 里 JACARA_LEARN=false 可以关掉。', { lessons })}
        >
          <Tag icon={<BulbOutlined />} color="cyan">
            {t('参考了以前改过的 {{lessons}} 处', { lessons })}
          </Tag>
        </Tooltip>
      )}
      {sent && extraction && (
        <SentTag documentId={documentId} extractionId={extraction.id} sent={sent} />
      )}
    </Flex>
  )
}

/**
 * Text sent to the model, expandable. If the value is absent here, OCR did not recognize it; if present, the
 * model missed it.
 */
function SentTag({
  documentId,
  extractionId,
  sent,
}: {
  documentId: string
  extractionId: string
  sent: NonNullable<Extraction['sent']>
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const text = useQuery({
    queryKey: ['sent-text', documentId, extractionId],
    queryFn: () => getSentText(documentId, extractionId),
    enabled: open,
    staleTime: Infinity,
  })
  const partial = sent.blocks < sent.total_blocks
  return (
    <Popover
      title={t('发给模型的单据文字')}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      content={
        <Flex vertical gap={8} style={{ width: 560, maxWidth: '80vw' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('{{blocks}} 个文字块、{{chars}} 字', { blocks: sent.blocks, chars: sent.chars })}
            {sent.images > 0 && t('，附了 {{images}} 张原图', { images: sent.images })}
            {sent.lessons > 0 && t('，带了 {{lessons}} 个以前改过的例子', { lessons: sent.lessons })}{t('。⟨数字⟩ 是文字块编号。 值不在这里面，是 OCR 没认出来；在里面却没取到，是模型漏了。')}
          </Typography.Text>
          {text.isLoading && <Spin size="small" />}
          {text.error && <Alert type="error" showIcon message={text.error.message} />}
          {text.data && (
            <pre
              style={{
                maxHeight: 360,
                overflow: 'auto',
                fontSize: 12,
                margin: 0,
                padding: 8,
                background: 'rgba(0,0,0,0.03)',
                whiteSpace: 'pre',
              }}
            >
              {text.data.text}
            </pre>
          )}
        </Flex>
      }
    >
      <Tag icon={<FileTextOutlined />} color={partial ? 'gold' : undefined} style={{ cursor: 'pointer' }}>
        {t('发给模型 {{blocks}}{{total}} 个文字块', { blocks: sent.blocks, total: partial ? ` / ${sent.total_blocks}` : '' })}
      </Tag>
    </Popover>
  )
}
