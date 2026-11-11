import { CloseOutlined, DeleteOutlined, ScanOutlined } from '@ant-design/icons'
import { Alert, Button, Flex, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useRef } from 'react'
import type { Extraction, FieldValue } from '../../api/types'
import { boxColor, fieldLabel, splitCell, type TableRow, tableLayouts } from './fields'
import { ItemsTable } from './ItemsTable'
import { ReviewForm } from './ReviewForm'
import { RevisionHistory } from './RevisionHistory'
import type { Revise } from './useRevise'

interface Props {
  extraction: Extraction
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** 能不能修改、确认（有"修改字段"权限）。 */
  canEdit: boolean
  /** 点了 🗑：清空这个字段（父组件弹框问原因）。 */
  onClear: (value: FieldValue, label: string) => void
  onAddRow: (table: string, after: number | null) => void
  onDeleteRow: (table: string, row: TableRow) => void
  onRestoreRow: (table: string, row: TableRow) => void
  onConfirmMany: (ids: string[]) => void
}

/**
 * 右边下半部分：规则校验结果、字段列表、商品明细表。点一个 = 选中它（详情显示在右边最上面）。
 * 选中的字段会自动滚到看得见的地方（键盘上下切换时也是）。
 */
export function FieldPanel({
  extraction,
  selectedId,
  hoveredId,
  onSelect,
  onHover,
  canEdit,
  onClear,
  onAddRow,
  onDeleteRow,
  onRestoreRow,
  onConfirmMany,
}: Props) {
  const { values } = extraction
  // 规则按审核之后的值重新检查过（删掉的行不算）；老接口没有时用抽取时的结果
  const checks = extraction.current_checks ?? extraction.checks
  const plain = values.filter((v) => splitCell(v.name) === null)
  const plainPending = plain.filter((v) => v.pending_review).length
  const layouts = tableLayouts(values)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (selectedId) {
      box.current
        ?.querySelector(`[data-value-id="${selectedId}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [selectedId])

  return (
    <Flex vertical gap={12} ref={box}>
      {checks.issues.map((issue, i) => (
        <Alert
          // 同一条规则可能报好几次（商品明细每一行单独检查）
          key={`${issue.rule}:${i}`}
          type={issue.severity === 'error' ? 'error' : 'warning'}
          showIcon
          title={issue.title}
          description={issue.message}
        />
      ))}
      <Typography.Text type="secondary">
        {plain.length} 个字段
        {plainPending > 0 ? `，${plainPending} 个等你确认（标黄）` : '，都不用确认'}
      </Typography.Text>
      <Flex vertical gap={4}>
        {plain.map((v) => (
          <FieldRow
            key={v.id}
            value={v}
            label={fieldLabel(v.name)}
            selected={v.id === selectedId}
            hovered={v.id === hoveredId}
            onSelect={() => onSelect(v.id)}
            onHover={(on) => onHover(on ? v.id : null)}
            canEdit={canEdit}
            onClear={() => onClear(v, fieldLabel(v.name))}
          />
        ))}
      </Flex>
      {layouts.map((layout) => (
        <ItemsTable
          key={layout.table}
          layout={layout}
          selectedId={selectedId}
          hoveredId={hoveredId}
          onSelect={onSelect}
          onHover={onHover}
          canEdit={canEdit}
          onAddRow={(after) => onAddRow(layout.table, after)}
          onDeleteRow={(row) => onDeleteRow(layout.table, row)}
          onRestoreRow={(row) => onRestoreRow(layout.table, row)}
          onConfirm={onConfirmMany}
        />
      ))}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        模型 {extraction.model} · 提示词 {extraction.prompt_version} · 文字来源{' '}
        {extraction.source === 'pdf-text' ? 'PDF 文字层' : 'OCR'}
      </Typography.Text>
    </Flex>
  )
}

/** 一个字段的状态标签：待确认、已确认、已修改、已从原文选取、已删除…… */
function StatusTags({ value: v }: { value: FieldValue }) {
  const edited = v.reviewed && v.current_value !== v.value && !v.deleted
  // 出处变了 = 从原文选取或框选过（只改值的话出处不变）
  const picked = v.reviewed && JSON.stringify(v.current_evidence) !== JSON.stringify(v.evidence)
  return (
    <>
      {v.pending_review && <Tag color="gold">待确认</Tag>}
      {v.reviewed && !edited && !v.deleted && <Tag color="green">已确认</Tag>}
      {edited && <Tag color="blue">{picked ? '已从原文选取' : '已修改'}</Tag>}
      {v.deleted && <Tag>已删除</Tag>}
      {/* 下面两个说的是模型原值；人审核过之后就不再显示，免得误会 */}
      {!v.reviewed && v.status === 'not_found' && <Tag color="red">原文里找不到</Tag>}
      {!v.reviewed && v.status === 'fuzzy' && <Tag>相似匹配</Tag>}
    </>
  )
}

function ShownValue({ value: v }: { value: FieldValue }) {
  return (
    <Typography.Text strong style={{ flex: 1, overflowWrap: 'anywhere' }}>
      {v.current_value ?? (
        <Typography.Text type="secondary">
          {v.origin === 'manual' ? '（待填写）' : '（原文没有）'}
        </Typography.Text>
      )}
    </Typography.Text>
  )
}

function ClearButton({ onClear }: { onClear: () => void }) {
  return (
    <Tooltip title="清空：原文里没有这个值（Delete 键）">
      <Button
        size="small"
        type="text"
        icon={<DeleteOutlined />}
        aria-label="清空"
        onClick={(e) => {
          e.stopPropagation()
          onClear()
        }}
      />
    </Tooltip>
  )
}

/** 列表里的一行（普通字段）。只显示名字、值和状态；详情和审核表单在右边最上面。 */
function FieldRow({
  value: v,
  label,
  selected,
  hovered,
  onSelect,
  onHover,
  canEdit,
  onClear,
}: {
  value: FieldValue
  label: string
  selected: boolean
  hovered: boolean
  onSelect: () => void
  onHover: (on: boolean) => void
  canEdit: boolean
  onClear: () => void
}) {
  return (
    <div
      data-value-id={v.id}
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
        <Typography.Text
          type="secondary"
          style={{ minWidth: 80, flexShrink: 0, whiteSpace: 'nowrap' }}
        >
          {label}
        </Typography.Text>
        <ShownValue value={v} />
        <Flex gap={4} wrap justify="end">
          <StatusTags value={v} />
          {canEdit && !v.deleted && v.current_value !== null && <ClearButton onClear={onClear} />}
        </Flex>
      </Flex>
      {v.pending_review && v.note && (
        <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0 88px', fontSize: 12 }}>
          {v.note}
        </Typography.Paragraph>
      )}
    </div>
  )
}

/**
 * 选中字段的详情：出处、调整框、审核表单、修改历史。固定在右边最上面，
 * 商品明细再长也不用往下翻。
 */
export function FieldDetail({
  value: v,
  label,
  canEdit,
  revise,
  onReviewed,
  onClear,
  onAdjustBox,
  onClose,
}: {
  value: FieldValue
  label: string
  canEdit: boolean
  revise: Revise
  onReviewed: () => void
  onClear: () => void
  onAdjustBox: () => void
  /** 收起详情（取消选中）。在空白处点一下也一样。 */
  onClose: () => void
}) {
  const ev = v.current_evidence ?? v.evidence
  const edited = v.reviewed && v.current_value !== v.value && !v.deleted
  const indent = { margin: '4px 0 0 0', fontSize: 12 }
  return (
    <div
      style={{
        padding: '10px 12px',
        borderRadius: 8,
        borderInlineStart: `4px solid ${boxColor(v)}`,
        background: '#f3effb',
        outline: '1px solid #d3c6f0',
      }}
    >
      <Flex justify="space-between" align="baseline" gap={8}>
        <Typography.Text type="secondary" style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
          {label}
        </Typography.Text>
        <ShownValue value={v} />
        <Flex gap={4} wrap justify="end">
          <StatusTags value={v} />
          {canEdit && !v.deleted && v.current_value !== null && <ClearButton onClear={onClear} />}
          <Tooltip title="收起（Esc，或在空白处点一下）">
            <Button size="small" type="text" icon={<CloseOutlined />} onClick={onClose} />
          </Tooltip>
        </Flex>
      </Flex>
      {v.note && (
        <Typography.Paragraph type="secondary" style={indent}>
          {v.note}
        </Typography.Paragraph>
      )}
      {ev && (
        <Typography.Paragraph type="secondary" style={indent}>
          出处：{ev.row ? `第 ${ev.row} 行` : ''}「{ev.row_text}」 · OCR 置信度{' '}
          {Math.round(ev.confidence * 100)}%
        </Typography.Paragraph>
      )}
      {edited && (
        <Typography.Paragraph type="secondary" style={indent}>
          模型原值：{v.value ?? '（原文没有）'}
        </Typography.Paragraph>
      )}
      {canEdit && !v.deleted && (
        <Button size="small" icon={<ScanOutlined />} onClick={onAdjustBox} style={{ marginTop: 4 }}>
          {ev ? '调整框 / 重新识别' : '在原图上框出来'}
        </Button>
      )}
      {/* key 带上当前值：改完之后表单用新值重新开始 */}
      {canEdit && !v.deleted && (
        <ReviewForm key={`${v.id}:${v.current_value}`} value={v} revise={revise} onDone={onReviewed} />
      )}
      {v.reviewed && <RevisionHistory valueId={v.id} />}
    </div>
  )
}
