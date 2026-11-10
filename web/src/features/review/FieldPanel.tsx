import { Alert, Flex, Tag, Typography } from 'antd'
import type { Extraction, FieldValue } from '../../api/types'
import { boxColor, fieldLabel } from './fields'
import { ReviewForm } from './ReviewForm'
import { RevisionHistory } from './RevisionHistory'
import type { Revise } from './useRevise'

interface Props {
  extraction: Extraction
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** 能不能修改、确认（有"修改字段"权限）。不能的话只显示，不显示表单。 */
  canEdit: boolean
  revise: Revise
  /** 一个字段审核完了。 */
  onReviewed: (id: string) => void
}

/** 右边：这一页的字段列表和规则校验结果。点字段 = 左边高亮它的出处，并展开审核表单。 */
export function FieldPanel({
  extraction,
  selectedId,
  hoveredId,
  onSelect,
  onHover,
  canEdit,
  revise,
  onReviewed,
}: Props) {
  const { checks, values } = extraction
  const pending = values.filter((v) => v.pending_review).length

  return (
    <Flex vertical gap={12}>
      {checks.issues.map((issue) => (
        <Alert
          key={issue.rule}
          type={issue.severity === 'error' ? 'error' : 'warning'}
          showIcon
          title={issue.title}
          description={issue.message}
        />
      ))}
      <Typography.Text type="secondary">
        {values.length} 个字段
        {pending > 0 ? `，${pending} 个等你确认（标黄）` : '，都不用确认'}
        {canEdit && ' · ↑↓ 切换字段，Enter 确认'}
      </Typography.Text>
      <Flex vertical gap={4}>
        {values.map((v) => (
          <FieldRow
            key={v.id}
            value={v}
            selected={v.id === selectedId}
            hovered={v.id === hoveredId}
            onSelect={() => onSelect(v.id)}
            onHover={(on) => onHover(on ? v.id : null)}
            canEdit={canEdit}
            revise={revise}
            onReviewed={() => onReviewed(v.id)}
          />
        ))}
      </Flex>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        模型 {extraction.model} · 提示词 {extraction.prompt_version} · 文字来源{' '}
        {extraction.source === 'pdf-text' ? 'PDF 文字层' : 'OCR'}
      </Typography.Text>
    </Flex>
  )
}

function FieldRow({
  value: v,
  selected,
  hovered,
  onSelect,
  onHover,
  canEdit,
  revise,
  onReviewed,
}: {
  value: FieldValue
  selected: boolean
  hovered: boolean
  onSelect: () => void
  onHover: (on: boolean) => void
  canEdit: boolean
  revise: Revise
  onReviewed: () => void
}) {
  const edited = v.reviewed && v.current_value !== v.value
  return (
    <div
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      style={{
        cursor: 'pointer',
        padding: '8px 12px',
        borderRadius: 8,
        borderInlineStart: `4px solid ${boxColor(v)}`,
        background: selected
          ? '#f3effb'
          : hovered
            ? '#faf9fc'
            : v.pending_review
              ? '#fffbe6'
              : '#fff',
        outline: selected ? '1px solid #d3c6f0' : 'none',
      }}
    >
      <Flex justify="space-between" align="baseline" gap={8}>
        <Typography.Text type="secondary" style={{ width: 80, flexShrink: 0 }}>
          {fieldLabel(v.name)}
        </Typography.Text>
        <Typography.Text strong style={{ flex: 1, wordBreak: 'break-all' }}>
          {v.current_value ?? <Typography.Text type="secondary">（原文没有）</Typography.Text>}
        </Typography.Text>
        <Flex gap={4} wrap justify="end">
          {v.pending_review && <Tag color="gold">待确认</Tag>}
          {v.reviewed && !edited && <Tag color="green">已确认</Tag>}
          {edited && <Tag color="blue">已修改</Tag>}
          {/* 下面两个说的是模型原值；人审核过之后就不再显示，免得误会 */}
          {!v.reviewed && v.status === 'not_found' && <Tag color="red">原文里找不到</Tag>}
          {!v.reviewed && v.status === 'fuzzy' && <Tag>相似匹配</Tag>}
        </Flex>
      </Flex>
      {(selected || v.pending_review) && v.note && (
        <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0 88px', fontSize: 12 }}>
          {v.note}
        </Typography.Paragraph>
      )}
      {selected && v.evidence && (
        <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0 88px', fontSize: 12 }}>
          出处：第 {v.evidence.row} 行「{v.evidence.row_text}」 · OCR 置信度{' '}
          {Math.round(v.evidence.confidence * 100)}%
        </Typography.Paragraph>
      )}
      {selected && edited && (
        <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0 88px', fontSize: 12 }}>
          模型原值：{v.value ?? '（原文没有）'}
        </Typography.Paragraph>
      )}
      {/* key 带上当前值：改完之后表单用新值重新开始 */}
      {selected && canEdit && (
        <ReviewForm key={`${v.id}:${v.current_value}`} value={v} revise={revise} onDone={onReviewed} />
      )}
      {selected && v.reviewed && <RevisionHistory valueId={v.id} />}
    </div>
  )
}
