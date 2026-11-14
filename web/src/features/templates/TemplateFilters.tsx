import { DownOutlined, SearchOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Flex, Input, Select, Tag, Typography } from 'antd'
import { useState } from 'react'
import type { Template } from '../../api/types'
import { countryName } from './labels'
import { domainsOf, type Filters, SOURCE_TEXT, sourceKind } from './filtering'
import { useTranslation } from 'react-i18next'

/** Number of tags shown per row by default; more appear after "Expand". */
const FIRST = 5

/**
 * A row of clickable tags: click to select, click again to deselect; "All" clears. With more than 5, only the
 * first 5 (plus selected ones) are shown, and a final button expands or collapses the rest.
 */
function ChipRow({
  label,
  items,
  selected,
  onToggle,
  onClear,
}: {
  label: string
  items: { value: string; text: string; count: number }[]
  selected: string[]
  onToggle: (value: string) => void
  onClear: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  const hidden = items.slice(FIRST).filter((i) => !selected.includes(i.value))
  const shown = open ? items : items.filter((i, n) => n < FIRST || selected.includes(i.value))
  return (
    <Flex gap={4} wrap align="center">
      <Typography.Text type="secondary" style={{ width: 72, flexShrink: 0 }}>
        {label}
      </Typography.Text>
      <Tag.CheckableTag checked={selected.length === 0} onChange={onClear}>
        {t('全部')}
      </Tag.CheckableTag>
      {shown.map((i) => (
        <Tag.CheckableTag
          key={i.value}
          checked={selected.includes(i.value)}
          onChange={() => onToggle(i.value)}
        >
          {t('{{text}}（{{count}}）', { text: i.text, count: i.count })}
        </Tag.CheckableTag>
      ))}
      {(open || hidden.length > 0) && (
        <Button
          type="link"
          size="small"
          icon={open ? <UpOutlined /> : <DownOutlined />}
          iconPosition="end"
          onClick={() => setOpen(!open)}
          style={{ paddingInline: 4 }}
        >
          {open ? t('收起') : t('展开其余 {{length}} 项', { length: hidden.length })}
        </Button>
      )}
    </Flex>
  )
}

function counted(values: string[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1)
  return counts
}

/**
 * Filters at the top of the templates page: search box plus three rows of clickable tags for domains,
 * countries and regions, and custom tags; my templates add source and sort order. Selecting a domain or
 * country narrows the list below.
 */
export function TemplateFilters({
  templates,
  value,
  onChange,
  mine,
}: {
  templates: Template[]
  value: Filters
  onChange: (f: Filters) => void
  /** My templates: additional custom tags, source and sort order. */
  mine: boolean
}) {
  const { t } = useTranslation()
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch })
  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
  const domains = [...counted(templates.flatMap(domainsOf))].map(([v, n]) => ({ value: v, text: v, count: n }))
  const general = templates.filter((tpl) => !(tpl.countries ?? []).length).length
  const countries = [
    ...(general ? [{ value: '', text: t('通用'), count: general }] : []),
    ...[...counted(templates.flatMap((tpl) => tpl.countries ?? []))].map(([v, n]) => ({
      value: v,
      text: countryName(v),
      count: n,
    })),
  ]
  const tags = [...counted(templates.flatMap((tpl) => tpl.tags ?? []))].map(([v, n]) => ({
    value: v,
    text: `#${v}`,
    count: n,
  }))
  const sources = [...counted(templates.map((tpl) => sourceKind(tpl.source)))]
  return (
    <Flex vertical gap={8}>
      <Flex gap={8} wrap align="center">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder={t('按名称、说明搜索')}
          value={value.q}
          onChange={(e) => set({ q: e.target.value })}
          style={{ width: 240 }}
        />
        {mine && sources.length > 1 && (
          <Select<string>
            allowClear
            placeholder={t('全部来源')}
            value={value.source}
            onChange={(source) => set({ source })}
            options={sources.map(([v, n]) => ({ value: v, label: t('{{value}}（{{n}}）', { value: SOURCE_TEXT[v] ?? v, n }) }))}
            style={{ width: 170 }}
          />
        )}
        {mine && (
          <Select<Filters['sort']>
            value={value.sort}
            onChange={(sort) => set({ sort })}
            options={[
              { value: 'updated', label: t('按修改时间排序') },
              { value: 'used_recent', label: t('按使用时间排序') },
              { value: 'used_most', label: t('按使用次数排序') },
              { value: 'name', label: t('按名称排序') },
            ]}
            style={{ width: 160 }}
          />
        )}
      </Flex>
      <ChipRow
        label={t('功能领域')}
        items={domains}
        selected={value.domains}
        onToggle={(v) => set({ domains: toggle(value.domains, v) })}
        onClear={() => set({ domains: [] })}
      />
      <ChipRow
        label={t('国家和地区')}
        items={countries}
        selected={value.country === undefined ? [] : [value.country]}
        onToggle={(v) => set({ country: value.country === v ? undefined : v })}
        onClear={() => set({ country: undefined })}
      />
      {mine && (
        <ChipRow
          label={t('自定义标签')}
          items={tags}
          selected={value.tags}
          onToggle={(v) => set({ tags: toggle(value.tags, v) })}
          onClear={() => set({ tags: [] })}
        />
      )}
    </Flex>
  )
}
