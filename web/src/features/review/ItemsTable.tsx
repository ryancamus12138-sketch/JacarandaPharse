import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CheckOutlined,
  DeleteOutlined,
  MessageOutlined,
  UndoOutlined,
  MoreOutlined,
  PlusOutlined,
  VerticalAlignBottomOutlined,
  VerticalAlignTopOutlined,
} from '@ant-design/icons'
import {
  Button,
  Dropdown,
  Flex,
  type MenuProps,
  Popconfirm,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { type CSSProperties, useState } from 'react'
import { boxColor, columnLabel, shownValue, type TableLayout, type TableRow, tableLabel } from './fields'
import { useTranslation } from 'react-i18next'

/** "Insert above" and "insert below" buttons in the row number cell. */
const insertButton: CSSProperties = {
  border: 'none',
  background: 'transparent',
  color: '#7b5cc4',
  cursor: 'pointer',
  padding: '1px 0',
  lineHeight: 1,
  display: 'flex',
  justifyContent: 'center',
  gap: 1,
}

interface Props {
  layout: TableLayout
  selectedId: string | null
  hoveredId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** Whether rows can be added and removed (requires the edit-fields permission). */
  canEdit: boolean
  /** Add a row after row after (real number; 0 inserts at the top; null appends at the end). */
  onAddRow: (after: number | null) => void
  onDeleteRow: (row: TableRow) => void
  onRestoreRow: (row: TableRow) => void
  /** Confirm these cells in bulk (their values are correct). */
  onConfirm: (ids: string[]) => void
  /** Unresolved comments per cell (field ID → count). */
  comments?: Record<string, number>
}

/**
 * Table field (line items): one row per item. Clicking a cell highlights its evidence on the left and opens
 * the review form below.
 * The ⋯ menu at the end of each row: insert above, insert below, delete (or restore) the row.
 */
export function ItemsTable({
  layout,
  selectedId,
  hoveredId,
  onSelect,
  onHover,
  canEdit,
  onAddRow,
  onDeleteRow,
  onRestoreRow,
  onConfirm,
  comments = {},
}: Props) {
  const { t } = useTranslation()
  const { table, columns, rows } = layout
  // Deleted rows stay in place (grey, struck through, without a row number, restorable).
  const live = rows.filter((r) => !r.deleted)
  const cells = live.flatMap((r) => [...r.cells.values()])
  const pendingIds = cells.filter((v) => v.pending_review).map((v) => v.id)
  const pending = pendingIds.length
  const border = '1px solid #f0f0f0'

  const [hoverRow, setHoverRow] = useState<number | null>(null)

  /** Row menu shared by the ⋯ button and right-click. */
  const menuFor = (row: TableRow): MenuProps => ({
    items: row.deleted
      ? [{ key: 'restore', icon: <UndoOutlined />, label: t('恢复这一行') }]
      : [
          { key: 'confirm', icon: <CheckOutlined />, label: t('确认这一行') },
          { type: 'divider' },
          { key: 'above', icon: <VerticalAlignTopOutlined />, label: t('在上方插入一行') },
          {
            key: 'below',
            icon: <VerticalAlignBottomOutlined />,
            label: t('在下方插入一行（⌘/Ctrl + Enter）'),
          },
          { type: 'divider' },
          { key: 'delete', icon: <DeleteOutlined />, label: t('删除这一行'), danger: true },
        ],
    onClick: ({ key }) => {
      if (key === 'above') onAddRow(above(row))
      if (key === 'below') onAddRow(row.index)
      if (key === 'delete') onDeleteRow(row)
      if (key === 'restore') onRestoreRow(row)
      if (key === 'confirm') {
        const cells = [...row.cells.values()]
        const ids = cells.filter((v) => !v.reviewed || v.pending_review).map((v) => v.id)
        onConfirm(ids.length > 0 ? ids : cells.map((v) => v.id))
      }
    },
  })

  /** Insert above: after the previous row, or at the top when this is the first row. */
  const above = (row: TableRow) => {
    const i = rows.indexOf(row)
    return i > 0 ? rows[i - 1].index : 0
  }

  return (
    <div>
      <Flex justify="space-between" align="center">
        <Typography.Text strong>
          {pending > 0
            ? t('{{tableLabel}}（{{length}} 行，{{pending}} 格等你确认）', { tableLabel: tableLabel(table), length: live.length, pending })
            : t('{{tableLabel}}（{{length}} 行）', { tableLabel: tableLabel(table), length: live.length })}
        </Typography.Text>
        {canEdit && (
          <Flex gap={4}>
            {pending > 0 && (
              // Shown only while cells are flagged; hidden once everything is handled.
              <Popconfirm
                title={t('确认这张表里 {{pending}} 格标黄的都是对的？', { pending })}
                description={t('不用一格一格点。每格都会记一条确认记录。')}
                okText={t('全部确认')}
                cancelText={t('取消')}
                onConfirm={() => onConfirm(pendingIds)}
              >
                <Button size="small" type="link" icon={<CheckOutlined />}>
                  {t('全部确认（{{pending}}）', { pending })}
                </Button>
              </Popconfirm>
            )}
            <Button
              size="small"
              type="link"
              icon={<PlusOutlined />}
              onClick={() => onAddRow(null)}
            >
              {t('加一行')}
            </Button>
          </Flex>
        )}
      </Flex>
      <div style={{ marginTop: 6 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            {/* Sticky header: column names stay visible when scrolled to row 30. */}
            <tr style={{ background: '#faf9fc', position: 'sticky', top: 0, zIndex: 1 }}>
              <th style={{ border, padding: '4px 6px', width: 32, fontWeight: 500 }}>#</th>
              {columns.map((c) => (
                <th key={c} style={{ border, padding: '4px 6px', fontWeight: 500 }}>
                  {columnLabel(c, table)}
                </th>
              ))}
              {canEdit && <th style={{ border, width: 28 }} />}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const hovering = canEdit && hoverRow === row.index && !row.deleted
              const tr = (
                <tr
                  key={row.index}
                  onMouseEnter={() => setHoverRow(row.index)}
                  onMouseLeave={() => setHoverRow(null)}
                  style={row.deleted ? { color: '#bbb', textDecoration: 'line-through' } : undefined}
                >
                  <td
                    style={{
                      border,
                      padding: hovering ? 0 : '4px 6px',
                      color: '#999',
                      textAlign: 'center',
                    }}
                  >
                    {hovering ? (
                      // As in Excel: hovering over a row turns its row number into "insert above" and "insert
                      // below" buttons.
                      <Flex vertical>
                        <Tooltip title={t('在上方插入一行')} placement="left">
                          <button
                            type="button"
                            style={insertButton}
                            aria-label={t('在上方插入一行')}
                            onClick={() => onAddRow(above(row))}
                          >
                            <PlusOutlined style={{ fontSize: 9 }} />
                            <ArrowUpOutlined style={{ fontSize: 9 }} />
                          </button>
                        </Tooltip>
                        <Tooltip title={t('在下方插入一行（⌘/Ctrl + Enter）')} placement="left">
                          <button
                            type="button"
                            style={insertButton}
                            aria-label={t('在下方插入一行')}
                            onClick={() => onAddRow(row.index)}
                          >
                            <PlusOutlined style={{ fontSize: 9 }} />
                            <ArrowDownOutlined style={{ fontSize: 9 }} />
                          </button>
                        </Tooltip>
                      </Flex>
                    ) : (
                      (row.display ?? '—')
                    )}
                  </td>
                  {columns.map((c) => {
                    const v = row.cells.get(c)
                    if (!v) {
                      return <td key={c} style={{ border }} />
                    }
                    const selected = v.id === selectedId
                    const edited = v.reviewed && v.current_value !== v.value && !row.deleted
                    return (
                      <td
                        key={c}
                        data-value-id={v.id}
                        onClick={() => onSelect(v.id)}
                        onMouseEnter={() => onHover(v.id)}
                        onMouseLeave={() => onHover(null)}
                        style={{
                          border,
                          padding: '4px 6px',
                          cursor: 'pointer',
                          overflowWrap: 'anywhere', // Break only long text, preferring spaces.
                          textAlign: v.type === 'number' ? 'right' : 'left',
                          boxShadow: row.deleted ? undefined : `inset 3px 0 0 ${boxColor(v)}`,
                          outline: selected ? '2px solid #b7a3e8' : 'none',
                          outlineOffset: -2,
                          background:
                            v.id === hoveredId
                              ? '#f3effb'
                              : row.deleted
                                ? '#fafafa'
                                : v.pending_review
                                  ? '#fffbe6'
                                  : edited
                                    ? '#e6f4ff'
                                    : '#fff',
                        }}
                      >
                        {shownValue(v) ?? <span style={{ color: '#bbb' }}>—</span>}
                        {(comments[v.name] ?? 0) > 0 && (
                          <MessageOutlined
                            aria-label={t('有备注')}
                            style={{ marginInlineStart: 4, color: '#7c5cbf', fontSize: 11 }}
                          />
                        )}
                      </td>
                    )
                  })}
                  {canEdit && (
                    <td style={{ border, textAlign: 'center', textDecoration: 'none' }}>
                      <Dropdown trigger={['click']} menu={menuFor(row)}>
                        <Button
                          size="small"
                          type="text"
                          icon={<MoreOutlined />}
                          aria-label={t('第 {{value}} 行的操作', { value: row.display ?? '' })}
                        />
                      </Dropdown>
                    </td>
                  )}
                </tr>
              )
              // Right-clicking a row opens the same menu (as in Excel).
              return canEdit ? (
                <Dropdown key={row.index} trigger={['contextMenu']} menu={menuFor(row)}>
                  {tr}
                </Dropdown>
              ) : (
                tr
              )
            })}
          </tbody>
        </table>
      </div>
      {live.some((r) => r.manual) && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          <Tag color="purple" style={{ marginInlineEnd: 4 }}>
            {t('人工')}
          </Tag>
          {t('新加的行先是空的：选中一格，再在原图上点文字填入，或者直接输入。')}
        </Typography.Text>
      )}
    </div>
  )
}
