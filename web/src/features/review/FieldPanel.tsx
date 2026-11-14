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
import { useTranslation } from 'react-i18next'
import { msg } from '../../i18n'

interface Props {
  extraction: Extraction
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** Whether fields can be edited and confirmed (requires the edit-fields permission). */
  canEdit: boolean
  /** 🗑 clicked: clear this field (the parent asks for a reason). */
  onClear: (value: FieldValue, label: string) => void
  onAddRow: (table: string, after: number | null) => void
  onDeleteRow: (table: string, row: TableRow) => void
  onRestoreRow: (table: string, row: TableRow) => void
  onConfirmMany: (ids: string[]) => void
  /**
   * Whether fields are ordered by document order or by status (extraction.values is already sorted
   * accordingly).
   */
  sortMode: SortMode
  onSortMode: (mode: SortMode) => void
  /** Unresolved comments per field on this page (field ID → count). */
  comments?: Record<string, number>
}

const LEVEL_SUMMARY: [Level, string, string][] = [
  ['red', 'red', msg('优先核对')],
  ['yellow', 'gold', msg('需核对')],
  ['green', 'green', msg('无异常')],
  ['grey', 'default', msg('原文缺失')],
]

/**
 * Lower right: rule results, field list, line items. Clicking selects an item (its details appear at the top
 * right).
 * The selected field scrolls into view automatically (also when moving with the arrow keys).
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
  const { t } = useTranslation()
  const { values } = extraction
  // Rules re-evaluated on post-review values (deleted rows excluded); older APIs without them fall back to
  // extraction-time results.
  const checks = extraction.current_checks ?? extraction.checks
  const plain = values.filter((v) => splitCell(v.name) === null)
  const plainPending = plain.filter((v) => v.pending_review).length
  // Color counts (fields + table cells, deleted rows excluded).
  // Manually added, unfilled cells are excluded (they are "to be filled", not "absent from the source").
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
          {t('{{length}} 个字段', { length: plain.length })}
          {plainPending > 0 ? t('，{{plainPending}} 个待确认', { plainPending }) : t('，无需确认')}
        </Typography.Text>
        {/* Changing the sort keeps the details open: this click does not count as a click on empty space. */}
        <Segmented<SortMode>
          data-keep-selection
          size="small"
          value={sortMode}
          onChange={onSortMode}
          options={[
            { label: t('按状态'), value: 'status' },
            { label: t('按单据顺序'), value: 'document' },
          ]}
        />
      </Flex>
      <Flex gap={4} wrap>
        {LEVEL_SUMMARY.map(([level, color, text]) => {
          const n = live.filter((v) => levelOf(v) === level).length
          return n > 0 ? (
            <Tag key={level} color={color} bordered={false}>
              {t(text)} {n}
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
        {t('模型 {{model}} · 提示词 {{prompt_version}} · 文字来源', { model: extraction.model, prompt_version: extraction.prompt_version })}{' '}
        {extraction.source === 'pdf-text' ? t('PDF 文字层') : 'OCR'}
      </Typography.Text>
    </Flex>
  )
}

/**
 * Status tags of a field. Two dimensions: evidence (found in the source) and validation (rules passed), plus
 * the review result: confirmed, edited, selected from source text, deleted.
 */
function StatusTags({ value: v, full = false }: { value: FieldValue; full?: boolean }) {
  const { t } = useTranslation()
  const edited = v.reviewed && v.current_value !== v.value && !v.deleted
  // Changed evidence means source text or region selection was used (editing only the value keeps the
  // evidence).
  const picked = v.reviewed && JSON.stringify(v.current_evidence) !== JSON.stringify(v.evidence)
  if (v.deleted) {
    return <Tag>{t('已删除')}</Tag>
  }
  // Evidence describes the model value; hidden after review to avoid confusion (still visible in the
  // details).
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
        <Tooltip title={t('此值并非从单据中抽取，而是按模板公式由其他字段计算得出；相关字段修改后将自动重新计算')}>
          <Tag color="cyan">{t('公式计算')}</Tag>
        </Tooltip>
      )}
      {v.status === 'seen' && !v.reviewed && (
        <Tooltip title={t('OCR 识别错误或未识别（手写、印章、模糊等），此值由模型直接读取原图得出，请对照原图核对')}>
          <Tag color="orange">{t('读取自原图')}</Tag>
        </Tooltip>
      )}
      {evidence}
      {check}
      {v.pending_review && <Tag color="gold">{t('待确认')}</Tag>}
      {v.suggestion && (
        <Tooltip
          title={
            v.suggestion.action === 'confirm'
              ? t('第 {{run}} 轮已人工确认相同的值', { run: v.suggestion.run })
              : t('第 {{run}} 轮模型抽取出相同的值，经人工修改为「{{value}}」', { run: v.suggestion.run, value: v.suggestion.value ?? t('（空）') })
          }
        >
          <Tag color="purple">
            {v.suggestion.action === 'confirm' ? t('上一轮已确认') : t('上一轮修改为 {{value}}', { value: v.suggestion.value ?? t('空') })}
          </Tag>
        </Tooltip>
      )}
      {v.reviewed && !edited && <Tag color="green">{t('已确认')}</Tag>}
      {edited && <Tag color="blue">{picked ? t('已从原文选取') : t('已修改')}</Tag>}
    </>
  )
}

/** Row background: red and yellow lightly tinted, others white. */
const TINT: Record<Level, string> = {
  red: '#fff1f0',
  yellow: '#fffbe6',
  grey: '#fff',
  green: '#fff',
}

function ShownValue({ value: v }: { value: FieldValue }) {
  const { t } = useTranslation()
  if (v.type === 'date_range' && v.current_value && v.range) {
    // Date range: start ~ end on top, number of days and source notation below.
    const r = v.range
    const quote = (v.current_evidence ?? v.evidence)?.quote
    const main = r.start && r.end ? `${r.start} ~ ${r.end}` : v.current_value
    const extra = [r.days ? t('{{days}} 天', { days: r.days }) : null, quote && quote !== main ? t('原文：{{quote}}', { quote }) : null]
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
            ? t('（待填写）')
            : v.origin === 'computed'
              ? t('（无法计算）')
            : v.rejected_value
              ? t('（未填写：模型输出的「{{rejected_value}}」与原文不符）', { rejected_value: v.rejected_value })
              : t('（原文缺失）')}
        </Typography.Text>
      )}
    </Typography.Text>
  )
}

function ClearButton({ onClear }: { onClear: () => void }) {
  const { t } = useTranslation()
  return (
    <Tooltip title={t('清空：原文中没有此值（Delete 键）')}>
      <Button
        size="small"
        type="text"
        icon={<DeleteOutlined />}
        aria-label={t('清空')}
        onClick={(e) => {
          e.stopPropagation()
          onClear()
        }}
      />
    </Tooltip>
  )
}

/**
 * A row in the list (regular field): name, value and status only; details and the review form appear at the
 * top right.
 */
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
  /** Unresolved comments on this field. */
  comments: number
}) {
  const { t } = useTranslation()
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
            <Tooltip title={t('{{comments}} 条备注未解决，点击上方“备注”查看', { comments })}>
              <MessageOutlined style={{ marginInlineStart: 4, color: '#7c5cbf' }} aria-label={t('有备注')} />
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
 * Details of the selected field: evidence, box adjustment, review form, history. Pinned at the top right so
 * long line-item tables need no scrolling.
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
  /** Document ID, needed for the zoomed images next to candidates. */
  documentId: string
  value: FieldValue
  label: string
  canEdit: boolean
  revise: Revise
  onReviewed: () => void
  onClear: () => void
  onAdjustBox: () => void
  /** Close the details (deselect). Clicking empty space does the same. */
  onClose: () => void
}) {
  const { t } = useTranslation()
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
          <Tooltip title={t('收起（Esc 或点击空白处）')}>
            <Button size="small" type="text" icon={<CloseOutlined />} onClick={onClose} />
          </Tooltip>
        </Flex>
      </Flex>
      {/*
       * The two dimensions on their own line: evidence (found in the source) × validation (rules passed),
       * plus the review result.
       */}
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
          {t('出处：')}
          {ev.blocks?.length
            ? t('文字块 {{join}}', { join: ev.blocks.map((n) => `⟨${n}⟩`).join('') })
            : ev.row
              ? t('第 {{row}} 行', { row: ev.row })
              : ''}
          {t('「{{row_text}}」 · OCR 置信度 {{round}}%', { row_text: ev.row_text, round: Math.round(ev.confidence * 100) })}
        </Typography.Paragraph>
      )}
      {edited && (
        <Typography.Paragraph type="secondary" style={indent}>
          {t('模型原值：')}{v.value ?? t('（原文缺失）')}
        </Typography.Paragraph>
      )}
      {canEdit && !v.deleted && (
        <Button size="small" icon={<ScanOutlined />} onClick={onAdjustBox} style={{ marginTop: 4 }}>
          {ev ? t('调整框 / 重新识别') : t('在原图上框选')}
        </Button>
      )}
      <Candidates
        documentId={documentId}
        value={v}
        revise={revise}
        canEdit={canEdit && !v.deleted}
        onDone={onReviewed}
      />
      {/* key includes the current value, so the form restarts with the new value after a change. */}
      {canEdit && !v.deleted && (
        <ReviewForm key={`${v.id}:${v.current_value}`} value={v} revise={revise} onDone={onReviewed} />
      )}
      {(v.reviewed || v.origin === 'computed') && <RevisionHistory valueId={v.id} />}
    </div>
  )
}
