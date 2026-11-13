import { BulbOutlined, EyeOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Flex, Popover, Tag, Tooltip, Typography } from 'antd'
import type { Extraction } from '../../api/types'

const BY_TEXT = { model: '模型选的', keywords: '按字段名、模板名对出来的', none: '没有可选的模板' }

/**
 * 这张单据是怎么处理的（ADR 0038）：自动判断选了哪个模板、为什么；有没有让模型看原图。
 * 都没有就什么也不显示。
 */
export function ExtractionNotes({ extraction }: { extraction: Extraction | undefined }) {
  const classified = extraction?.classified
  const vision = extraction?.vision
  // 从人改过的地方学（ADR 0045）：提示词版本记成 extract-v8+lessons3
  const lessons = Number(/\+lessons(\d+)/.exec(extraction?.prompt_version ?? '')?.[1] ?? 0)
  if (!classified && !vision && !lessons) return null
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
    </Flex>
  )
}
