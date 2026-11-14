import { CheckCircleOutlined, FunctionOutlined, WarningOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Button, Dropdown, Input, type InputRef, Space, Tooltip, Typography, type MenuProps } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { checkFormula } from '../../api/templates'
import type { TemplateField, TemplateTable } from '../../api/types'
import { useTranslation } from 'react-i18next'

/**
 * Input for formulas and rule conditions.
 * - The button on the right lists the template's current fields (display name + field ID), table totals and
 *   functions; clicking inserts at the cursor, so IDs need not be memorized;
 * - Live validation: the line below shows the formula with display names (Check-out date − Check-in date) and
 *   explains errors.
 * kind="formula" is a computed field formula; "condition" is a rule condition (requiring a comparison such as
 * == < >). row is a table name: for table column formulas and per-row rules, names resolve to that table's
 * columns.
 */
export function FormulaInput({
  value,
  onChange,
  disabled,
  kind,
  fields,
  tables,
  row,
  self,
  placeholder,
}: {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  kind: 'formula' | 'condition'
  fields: TemplateField[]
  tables: TemplateTable[]
  row: string | null
  /** The field this formula belongs to (excluded from the list; a field cannot compute itself). */
  self?: string
  placeholder?: string
}) {
  const { t } = useTranslation()
  const input = useRef<InputRef>(null)
  const [text, setText] = useState(value)
  // Validate after a 0.4-second pause rather than on every keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setText(value), 400)
    return () => clearTimeout(timer)
  }, [value])
  const check = useQuery({
    queryKey: ['formula-check', kind, row, text, fields, tables],
    queryFn: () => checkFormula({ text, kind, fields, tables, row }),
    enabled: text.trim() !== '',
    staleTime: Infinity,
  })

  const insert = (piece: string) => {
    const el = input.current?.input
    const start = el?.selectionStart ?? value.length
    const end = el?.selectionEnd ?? value.length
    const before = value.slice(0, start)
    const glue = before && !/[\s(]$/.test(before) ? ' ' : ''
    const next = `${before}${glue}${piece}${value.slice(end)}`
    onChange(next)
    const caret = before.length + glue.length + piece.length
    setTimeout(() => {
      input.current?.focus()
      input.current?.input?.setSelectionRange(caret, caret)
    })
  }

  const usable = (f: TemplateField) => (f.type === 'number' || f.type === 'date') && f.name !== self
  const item = (key: string, label: string, code: string) => ({
    key,
    label: (
      <Space size={8}>
        <span>{label}</span>
        <Typography.Text code style={{ fontSize: 12 }}>
          {code}
        </Typography.Text>
      </Space>
    ),
  })
  const rowTable = tables.find((tbl) => tbl.name === row)
  const groups: MenuProps['items'] = []
  if (rowTable) {
    groups.push({
      type: 'group',
      label: t('{{label}}当前行的列', { label: rowTable.label }),
      children: rowTable.columns.filter(usable).map((c) => item(c.name, c.label, c.name)),
    })
  }
  if (!rowTable || kind === 'formula') {
    groups.push({
      type: 'group',
      label: t('字段（仅列出数字和日期）'),
      children: [
        ...fields.filter(usable).map((f) => item(f.name, f.label, f.name)),
        // Date range: start and end are dates; days is a number.
        ...fields
          .filter((f) => f.type === 'date_range' && f.name !== self)
          .flatMap((f) => [
            item(`${f.name}.start`, t('{{label}}的开始', { label: f.label }), `${f.name}.start`),
            item(`${f.name}.end`, t('{{label}}的结束', { label: f.label }), `${f.name}.end`),
            item(`${f.name}.days`, t('{{label}}的天数', { label: f.label }), `${f.name}.days`),
          ]),
      ],
    })
  }
  if (!rowTable) {
    const sums = tables.flatMap((tbl) => [
      ...tbl.columns
        .filter((c) => c.type === 'number')
        .map((c) => item(`sum(${tbl.name}.${c.name})`, t('{{label}}的{{label2}}合计', { label: tbl.label, label2: c.label }), `sum(${tbl.name}.${c.name})`)),
      item(`count(${tbl.name})`, t('{{label}}的行数', { label: tbl.label }), `count(${tbl.name})`),
    ])
    if (sums.length) groups.push({ type: 'group', label: t('表格'), children: sums })
  }
  groups.push({
    type: 'group',
    label: t('算式和函数'),
    children: [
      ...(kind === 'condition'
        ? [
            item('==', t('等于'), '=='),
            item('<=', t('不超过'), '<='),
            item('>=', t('不少于'), '>='),
            item('>', t('大于'), '>'),
            item('<', t('小于'), '<'),
            item('!=', t('不等于'), '!='),
          ]
        : []),
      item('round(', t('四舍五入'), 'round(x, 2)'),
      item('abs(', t('绝对值'), 'abs(x)'),
      item('min(', t('取较小值'), 'min(a, b)'),
      item('max(', t('取较大值'), 'max(a, b)'),
    ],
  })

  const result = !value.trim() ? null : check.data && text === value ? check.data : null
  return (
    <div style={{ width: '100%' }}>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          ref={input}
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          status={result && !result.ok ? 'error' : undefined}
          onChange={(e) => onChange(e.target.value)}
          style={{ fontFamily: 'monospace' }}
        />
        {!disabled && (
          <Dropdown
            trigger={['click']}
            menu={{ items: groups, onClick: ({ key }) => insert(key), style: { maxHeight: 360, overflowY: 'auto' } }}
          >
            <Tooltip title={t('插入字段或函数：列出此模板当前的全部字段')}>
              <Button icon={<FunctionOutlined />} aria-label={t('插入字段或函数')} />
            </Tooltip>
          </Dropdown>
        )}
      </Space.Compact>
      {result && (
        <Typography.Text
          type={result.ok ? 'secondary' : 'danger'}
          style={{ fontSize: 12, display: 'block', marginTop: 2 }}
        >
          {result.ok ? (
            <>
              <CheckCircleOutlined /> {kind === 'formula' ? '= ' : ''}
              {result.readable}
            </>
          ) : (
            <>
              <WarningOutlined /> {result.message}
            </>
          )}
        </Typography.Text>
      )}
    </div>
  )
}
