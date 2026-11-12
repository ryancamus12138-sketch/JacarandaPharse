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
import { boxColor, columnLabel, type TableLayout, type TableRow, tableLabel } from './fields'

/** 行号格子里"上面加一行""下面加一行"的小按钮。 */
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
  /** 能不能增删行（有"修改字段"权限）。 */
  canEdit: boolean
  /** 加一行：插在第 after 行后面（真实编号；0 是最前面；null 是最后）。 */
  onAddRow: (after: number | null) => void
  onDeleteRow: (row: TableRow) => void
  onRestoreRow: (row: TableRow) => void
  /** 批量确认这些格子（值是对的）。 */
  onConfirm: (ids: string[]) => void
  /** 每一格还没解决的备注几条（字段 ID → 条数）。 */
  comments?: Record<string, number>
}

/**
 * 表格字段（商品明细）：一行商品一行。点一格 = 左边高亮它的出处，下面展开审核表单。
 * 每行最后的 ⋯ 菜单：在上方插入、在下方插入、删除（或恢复）这一行。
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
  const { table, columns, rows } = layout
  // 删掉的行留在原位（灰色、划掉，不占行号，可以恢复）
  const live = rows.filter((r) => !r.deleted)
  const cells = live.flatMap((r) => [...r.cells.values()])
  const pendingIds = cells.filter((v) => v.pending_review).map((v) => v.id)
  const pending = pendingIds.length
  const border = '1px solid #f0f0f0'

  const [hoverRow, setHoverRow] = useState<number | null>(null)

  /** 一行的菜单：⋯ 按钮和右键用同一个。 */
  const menuFor = (row: TableRow): MenuProps => ({
    items: row.deleted
      ? [{ key: 'restore', icon: <UndoOutlined />, label: '恢复这一行' }]
      : [
          { key: 'confirm', icon: <CheckOutlined />, label: '确认这一行' },
          { type: 'divider' },
          { key: 'above', icon: <VerticalAlignTopOutlined />, label: '在上方插入一行' },
          {
            key: 'below',
            icon: <VerticalAlignBottomOutlined />,
            label: '在下方插入一行（⌘/Ctrl + Enter）',
          },
          { type: 'divider' },
          { key: 'delete', icon: <DeleteOutlined />, label: '删除这一行', danger: true },
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

  /** 上方插入：插在上一行后面；已经是第一行就插在最前面。 */
  const above = (row: TableRow) => {
    const i = rows.indexOf(row)
    return i > 0 ? rows[i - 1].index : 0
  }

  return (
    <div>
      <Flex justify="space-between" align="center">
        <Typography.Text strong>
          {tableLabel(table)}（{live.length} 行{pending > 0 ? `，${pending} 格等你确认` : ''}）
        </Typography.Text>
        {canEdit && (
          <Flex gap={4}>
            {pending > 0 && (
              // 只在有标黄的格子时出现；全都处理完了就不显示
              <Popconfirm
                title={`确认这张表里 ${pending} 格标黄的都是对的？`}
                description="不用一格一格点。每格都会记一条确认记录。"
                okText="全部确认"
                cancelText="取消"
                onConfirm={() => onConfirm(pendingIds)}
              >
                <Button size="small" type="link" icon={<CheckOutlined />}>
                  全部确认（{pending}）
                </Button>
              </Popconfirm>
            )}
            <Button
              size="small"
              type="link"
              icon={<PlusOutlined />}
              onClick={() => onAddRow(null)}
            >
              加一行
            </Button>
          </Flex>
        )}
      </Flex>
      <div style={{ marginTop: 6 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            {/* 表头粘在上面：往下滚到第 30 行也看得到每一列是什么 */}
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
                      // 像 Excel：鼠标停在一行上，行号变成"上面加一行""下面加一行"两个小按钮
                      <Flex vertical>
                        <Tooltip title="在上方插入一行" placement="left">
                          <button
                            type="button"
                            style={insertButton}
                            aria-label="在上方插入一行"
                            onClick={() => onAddRow(above(row))}
                          >
                            <PlusOutlined style={{ fontSize: 9 }} />
                            <ArrowUpOutlined style={{ fontSize: 9 }} />
                          </button>
                        </Tooltip>
                        <Tooltip title="在下方插入一行（⌘/Ctrl + Enter）" placement="left">
                          <button
                            type="button"
                            style={insertButton}
                            aria-label="在下方插入一行"
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
                          overflowWrap: 'anywhere', // 长的才断开，先在空格处换行
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
                        {v.current_value ?? <span style={{ color: '#bbb' }}>—</span>}
                        {(comments[v.name] ?? 0) > 0 && (
                          <MessageOutlined
                            aria-label="有备注"
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
                          aria-label={`第 ${row.display ?? ''} 行的操作`}
                        />
                      </Dropdown>
                    </td>
                  )}
                </tr>
              )
              // 右键一行也能打开同样的菜单（和 Excel 一样）
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
            人工
          </Tag>
          新加的行先是空的：选中一格，再在原图上点文字填入，或者直接输入。
        </Typography.Text>
      )}
    </div>
  )
}
