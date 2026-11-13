import { CloseOutlined, DeleteOutlined, MessageOutlined, ScanOutlined } from '@ant-design/icons'
import { Button, Flex, Segmented, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useRef } from 'react'
import type { Extraction, FieldValue, Level } from '../../api/types'
import {
  boxColor,
  CHECK_TEXT,
  EVIDENCE_TEXT,
  fieldLabel,
  levelOf,
  type SortMode,
  splitCell,
  type TableRow,
  tableLayouts,
  shownValue,
} from './fields'
import { IssueToggle } from './IssueToggle'
import { ItemsTable } from './ItemsTable'
import { Candidates } from './Candidates'
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
  /** 字段按单据顺序还是按状态排（extraction.values 已经按这个排好了）。 */
  sortMode: SortMode
  onSortMode: (mode: SortMode) => void
  /** 这一页每个字段还没解决的备注几条（字段 ID → 条数）。 */
  comments?: Record<string, number>
}

const LEVEL_SUMMARY: [Level, string, string][] = [
  ['red', 'red', '先看'],
  ['yellow', 'gold', '看一眼'],
  ['green', 'green', '没问题'],
  ['grey', 'default', '原文没有'],
]

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
  sortMode,
  onSortMode,
  comments = {},
}: Props) {
  const { values } = extraction
  // 规则按审核之后的值重新检查过（删掉的行不算）；老接口没有时用抽取时的结果
  const checks = extraction.current_checks ?? extraction.checks
  const plain = values.filter((v) => splitCell(v.name) === null)
  const plainPending = plain.filter((v) => v.pending_review).length
  // 颜色统计（字段 + 表格的格子，删掉的行不算）
  // 人工新加、还没填的格子不算（它们是"待填写"，不是"原文没有"）
  const live = values.filter((v) => !v.deleted && !(v.origin === 'manual' && v.current_value === null))
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
      <IssueToggle checks={checks} />
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Typography.Text type="secondary">
          {plain.length} 个字段
          {plainPending > 0 ? `，${plainPending} 个等你确认` : '，都不用确认'}
        </Typography.Text>
        {/* 换排序不用收起详情：点这里不算"点了空白处" */}
        <Segmented<SortMode>
          data-keep-selection
          size="small"
          value={sortMode}
          onChange={onSortMode}
          options={[
            { label: '按状态', value: 'status' },
            { label: '按单据顺序', value: 'document' },
          ]}
        />
      </Flex>
      <Flex gap={4} wrap>
        {LEVEL_SUMMARY.map(([level, color, text]) => {
          const n = live.filter((v) => levelOf(v) === level).length
          return n > 0 ? (
            <Tag key={level} color={color} bordered={false}>
              {text} {n}
            </Tag>
          ) : null
        })}
      </Flex>
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
            comments={comments[v.name] ?? 0}
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
          comments={comments}
        />
      ))}
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        模型 {extraction.model} · 提示词 {extraction.prompt_version} · 文字来源{' '}
        {extraction.source === 'pdf-text' ? 'PDF 文字层' : 'OCR'}
      </Typography.Text>
    </Flex>
  )
}

/**
 * 一个字段的状态标签。两个维度（ADR 0028）：证据（原文里找到没有）、校验（规则过没过）；
 * 再加审核的结果：已确认、已修改、已从原文选取、已删除。
 */
function StatusTags({ value: v, full = false }: { value: FieldValue; full?: boolean }) {
  const edited = v.reviewed && v.current_value !== v.value && !v.deleted
  // 出处变了 = 从原文选取或框选过（只改值的话出处不变）
  const picked = v.reviewed && JSON.stringify(v.current_evidence) !== JSON.stringify(v.evidence)
  if (v.deleted) {
    return <Tag>已删除</Tag>
  }
  // 证据说的是模型原值；人审核过之后就不再显示，免得误会（详情里还看得到）
  const evidence =
    full || !v.reviewed ? (
      v.evidence_state === 'missing' ? (
        <Tag color="red">{EVIDENCE_TEXT.missing}</Tag>
      ) : v.evidence_state === 'pending' ? (
        <Tag color="gold">{EVIDENCE_TEXT.pending}</Tag>
      ) : full && v.evidence_state === 'verified' && v.origin !== 'computed' ? (
        <Tag color="green">{EVIDENCE_TEXT.verified}</Tag>
      ) : null
    ) : null
  const check =
    v.check_state === 'fail' ? (
      <Tag color="red">{CHECK_TEXT.fail}</Tag>
    ) : v.check_state === 'warn' ? (
      <Tag color="gold">{CHECK_TEXT.warn}</Tag>
    ) : full && v.check_state === 'ok' ? (
      <Tag color="green">{CHECK_TEXT.ok}</Tag>
    ) : null
  return (
    <>
      {v.origin === 'computed' && (
        <Tooltip title="不是从单据上找的，是按模板的公式由别的字段算出来的；那些字段改了会跟着重算">
          <Tag color="cyan">按公式算</Tag>
        </Tooltip>
      )}
      {v.status === 'seen' && !v.reviewed && (
        <Tooltip title="OCR 认错了或没认出来（手写、印章、模糊），这个值是模型看着原图读的，请对着原图核对">
          <Tag color="orange">看原图读的</Tag>
        </Tooltip>
      )}
      {evidence}
      {check}
      {v.pending_review && <Tag color="gold">待确认</Tag>}
      {v.suggestion && (
        <Tooltip
          title={
            v.suggestion.action === 'confirm'
              ? `第 ${v.suggestion.run} 轮人确认过同样的值`
              : `第 ${v.suggestion.run} 轮模型抽出同样的值，被人改成了「${v.suggestion.value ?? '（空）'}」`
          }
        >
          <Tag color="purple">
            {v.suggestion.action === 'confirm' ? '上次确认过' : `上次改成 ${v.suggestion.value ?? '空'}`}
          </Tag>
        </Tooltip>
      )}
      {v.reviewed && !edited && <Tag color="green">已确认</Tag>}
      {edited && <Tag color="blue">{picked ? '已从原文选取' : '已修改'}</Tag>}
    </>
  )
}

/** 列表里一行的底色：红的、黄的淡淡地标出来，其他白色。 */
const TINT: Record<Level, string> = {
  red: '#fff1f0',
  yellow: '#fffbe6',
  grey: '#fff',
  green: '#fff',
}

function ShownValue({ value: v }: { value: FieldValue }) {
  if (v.type === 'date_range' && v.current_value && v.range) {
    // 日期范围（ADR 0039、0040）：上面开始 ~ 结束，下面天数和原文的写法
    const r = v.range
    const quote = (v.current_evidence ?? v.evidence)?.quote
    const main = r.start && r.end ? `${r.start} ~ ${r.end}` : v.current_value
    const extra = [r.days ? `${r.days} 天` : null, quote && quote !== main ? `原文：${quote}` : null]
      .filter(Boolean)
      .join(' · ')
    return (
      <Flex vertical style={{ flex: 1, minWidth: 0 }}>
        <Typography.Text strong style={{ overflowWrap: 'anywhere' }}>
          {main}
        </Typography.Text>
        {extra && (
          <Typography.Text type="secondary" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
            {extra}
          </Typography.Text>
        )}
        {r.problem && (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            {r.problem}
          </Typography.Text>
        )}
      </Flex>
    )
  }
  return (
    <Typography.Text strong style={{ flex: 1, overflowWrap: 'anywhere' }}>
      {shownValue(v) ?? (
        <Typography.Text type="secondary">
          {v.origin === 'manual'
            ? '（待填写）'
            : v.origin === 'computed'
              ? '（算不出来）'
            : v.rejected_value
              ? `（没有填：模型给的「${v.rejected_value}」原文里对不上）`
              : '（原文没有）'}
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
  comments,
}: {
  value: FieldValue
  label: string
  selected: boolean
  hovered: boolean
  onSelect: () => void
  onHover: (on: boolean) => void
  canEdit: boolean
  onClear: () => void
  /** 这个字段还没解决的备注几条。 */
  comments: number
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
            : TINT[levelOf(v)],
        outline: selected ? '1px solid #d3c6f0' : 'none',
      }}
    >
      <Flex justify="space-between" align="baseline" gap={8}>
        <Typography.Text
          type="secondary"
          style={{ minWidth: 80, flexShrink: 0, whiteSpace: 'nowrap' }}
        >
          {label}
          {comments > 0 && (
            <Tooltip title={`${comments} 条备注没解决，点上面的"备注"看`}>
              <MessageOutlined style={{ marginInlineStart: 4, color: '#7c5cbf' }} aria-label="有备注" />
            </Tooltip>
          )}
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
  documentId,
}: {
  /** 文档 ID：候选值旁边的局部图要用。 */
  documentId: string
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
        <Flex gap={4} justify="end" style={{ flexShrink: 0 }}>
          {canEdit && !v.deleted && v.current_value !== null && <ClearButton onClear={onClear} />}
          <Tooltip title="收起（Esc，或在空白处点一下）">
            <Button size="small" type="text" icon={<CloseOutlined />} onClick={onClose} />
          </Tooltip>
        </Flex>
      </Flex>
      {/* 两个维度单独一行：证据（原文里找到没有）× 校验（规则过没过），再加审核结果 */}
      <Flex gap={4} wrap style={{ marginTop: 6 }}>
        <StatusTags value={v} full />
      </Flex>
      {v.note && (
        <Typography.Paragraph type="secondary" style={indent}>
          {v.note}
        </Typography.Paragraph>
      )}
      {ev && (
        <Typography.Paragraph type="secondary" style={indent}>
          出处：
          {ev.blocks?.length
            ? `文字块 ${ev.blocks.map((n) => `⟨${n}⟩`).join('')}`
            : ev.row
              ? `第 ${ev.row} 行`
              : ''}
          「{ev.row_text}」 · OCR 置信度{' '}
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
      <Candidates
        documentId={documentId}
        value={v}
        revise={revise}
        canEdit={canEdit && !v.deleted}
        onDone={onReviewed}
      />
      {/* key 带上当前值：改完之后表单用新值重新开始 */}
      {canEdit && !v.deleted && (
        <ReviewForm key={`${v.id}:${v.current_value}`} value={v} revise={revise} onDone={onReviewed} />
      )}
      {(v.reviewed || v.origin === 'computed') && <RevisionHistory valueId={v.id} />}
    </div>
  )
}
