import { BulbOutlined, EyeOutlined, FileTextOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Flex, Popover, Spin, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { getSentText } from '../../api/documents'
import type { Extraction } from '../../api/types'

const BY_TEXT = { model: '模型选的', keywords: '按字段名、模板名对出来的', none: '没有可选的模板' }

/**
 * 这张单据是怎么处理的（ADR 0038）：自动判断选了哪个模板、为什么；有没有让模型看原图。
 * 都没有就什么也不显示。
 */
export function ExtractionNotes({
  extraction,
  documentId,
}: {
  extraction: Extraction | undefined
  documentId: string
}) {
  const classified = extraction?.classified
  const vision = extraction?.vision
  // 从人改过的地方学（ADR 0045）：提示词版本记成 extract-v8+lessons3
  const lessons = Number(/\+(?:lessons|curated)(\d+)/.exec(extraction?.prompt_version ?? '')?.[1] ?? 0)
  const sent = extraction?.sent
  if (!classified && !vision && !lessons && !sent) return null
  return (
    <Flex gap={8} wrap align="center" style={{ marginBottom: 12 }}>
      {classified && (
        <Popover
          title="自动判断用哪个模板"
          content={
            <Flex vertical gap={6} style={{ maxWidth: 360 }}>
              <Typography.Text>{classified.reason}</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {BY_TEXT[classified.by]}
              </Typography.Text>
              {classified.candidates.length > 0 && (
                <>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    比较过的模板（按字段名对上的多少排）：
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
                选得不对：点"重新处理"，换一个模板。
              </Typography.Text>
            </Flex>
          }
        >
          <Tag icon={<ThunderboltOutlined />} color="purple" style={{ cursor: 'pointer' }}>
            自动判断：{classified.picked ? classified.picked.title : '没有合适的模板'}
          </Tag>
        </Popover>
      )}
      {vision && (
        <Tooltip title={`${vision}。OCR 文字里对不上的值标了"看原图读的"，请对着原图核对。`}>
          <Tag icon={<EyeOutlined />} color="orange">
            模型看了原图
          </Tag>
        </Tooltip>
      )}
      {lessons > 0 && (
        <Tooltip
          title={`抽取时告诉了模型：同一个模板以前人工改过的 ${lessons} 个地方（容易错在哪），值仍然按这张单据的原文。在 .env 里 JACARA_LEARN=false 可以关掉。`}
        >
          <Tag icon={<BulbOutlined />} color="cyan">
            参考了以前改过的 {lessons} 处
          </Tag>
        </Tooltip>
      )}
      {sent && extraction && (
        <SentTag documentId={documentId} extractionId={extraction.id} sent={sent} />
      )}
    </Flex>
  )
}

/** 发给模型的是哪些文字（ADR 0053）：点开看原文。值不在这里面，是 OCR 没认出来；在里面，是模型漏了。 */
function SentTag({
  documentId,
  extractionId,
  sent,
}: {
  documentId: string
  extractionId: string
  sent: NonNullable<Extraction['sent']>
}) {
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
      title="发给模型的单据文字"
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      content={
        <Flex vertical gap={8} style={{ width: 560, maxWidth: '80vw' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {sent.blocks} 个文字块、{sent.chars} 字
            {sent.images > 0 && `，附了 ${sent.images} 张原图`}
            {sent.lessons > 0 && `，带了 ${sent.lessons} 个以前改过的例子`}。⟨数字⟩ 是文字块编号。
            值不在这里面，是 OCR 没认出来；在里面却没取到，是模型漏了。
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
        发给模型 {sent.blocks}
        {partial ? ` / ${sent.total_blocks}` : ''} 个文字块
      </Tag>
    </Popover>
  )
}
