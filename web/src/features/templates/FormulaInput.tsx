import { CheckCircleOutlined, FunctionOutlined, WarningOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Button, Dropdown, Input, type InputRef, Space, Tooltip, Typography, type MenuProps } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { checkFormula } from '../../api/templates'
import type { TemplateField, TemplateTable } from '../../api/types'

/**
 * 写公式或规则条件的输入框（ADR 0035）。
 * - 右边的按钮列出这个模板现在有哪些字段（中文名 + 字段 ID）、表格合计和函数，点一下插到光标处，不用记 ID；
 * - 边写边检查：下面一行显示换成中文名的样子（离店日期 − 入住日期），写错了说哪里错。
 * kind="formula" 是计算字段的公式；"condition" 是规则的条件（要有 == < > 这些比较）。
 * row 写表名：表格的列的公式、"每一行"的规则，名字按这张表的列找。
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
  /** 正在写公式的这个字段自己（不列出来，不能自己算自己）。 */
  self?: string
  placeholder?: string
}) {
  const input = useRef<InputRef>(null)
  const [text, setText] = useState(value)
  // 停下来 0.4 秒再检查，不是每敲一个字就问一次
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
  const rowTable = tables.find((t) => t.name === row)
  const groups: MenuProps['items'] = []
  if (rowTable) {
    groups.push({
      type: 'group',
      label: `${rowTable.label}这一行的列`,
      children: rowTable.columns.filter(usable).map((c) => item(c.name, c.label, c.name)),
    })
  }
  if (!rowTable || kind === 'formula') {
    groups.push({
      type: 'group',
      label: '字段（只列数字和日期）',
      children: [
        ...fields.filter(usable).map((f) => item(f.name, f.label, f.name)),
        // 日期范围（ADR 0039）：开始、结束是日期，天数是数字
        ...fields
          .filter((f) => f.type === 'date_range' && f.name !== self)
          .flatMap((f) => [
            item(`${f.name}.start`, `${f.label}的开始`, `${f.name}.start`),
            item(`${f.name}.end`, `${f.label}的结束`, `${f.name}.end`),
            item(`${f.name}.days`, `${f.label}的天数`, `${f.name}.days`),
          ]),
      ],
    })
  }
  if (!rowTable) {
    const sums = tables.flatMap((t) => [
      ...t.columns
        .filter((c) => c.type === 'number')
        .map((c) => item(`sum(${t.name}.${c.name})`, `${t.label}的${c.label}合计`, `sum(${t.name}.${c.name})`)),
      item(`count(${t.name})`, `${t.label}有几行`, `count(${t.name})`),
    ])
    if (sums.length) groups.push({ type: 'group', label: '表格', children: sums })
  }
  groups.push({
    type: 'group',
    label: '算式和函数',
    children: [
      ...(kind === 'condition'
        ? [
            item('==', '等于', '=='),
            item('<=', '不超过', '<='),
            item('>=', '不少于', '>='),
            item('>', '大于', '>'),
            item('<', '小于', '<'),
            item('!=', '不等于', '!='),
          ]
        : []),
      item('round(', '四舍五入', 'round(x, 2)'),
      item('abs(', '绝对值', 'abs(x)'),
      item('min(', '取小的', 'min(a, b)'),
      item('max(', '取大的', 'max(a, b)'),
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
            <Tooltip title="插入字段或函数：列出这个模板现在有哪些字段">
              <Button icon={<FunctionOutlined />} aria-label="插入字段或函数" />
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
