import { SearchOutlined } from '@ant-design/icons'
import { Flex, Input, Select, Tag, Typography } from 'antd'
import type { Template } from '../../api/types'
import { countryName } from './labels'
import { domainsOf, type Filters, SOURCE_TEXT, sourceKind } from './filtering'

/** 一排可以点的标签（像微信的标签分类）：点一下选上，再点取消；"全部"清空。 */
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
  if (items.length === 0) return null
  return (
    <Flex gap={4} wrap align="center">
      <Typography.Text type="secondary" style={{ width: 72, flexShrink: 0 }}>
        {label}
      </Typography.Text>
      <Tag.CheckableTag checked={selected.length === 0} onChange={onClear}>
        全部
      </Tag.CheckableTag>
      {items.map((i) => (
        <Tag.CheckableTag
          key={i.value}
          checked={selected.includes(i.value)}
          onChange={() => onToggle(i.value)}
        >
          {i.text}（{i.count}）
        </Tag.CheckableTag>
      ))}
    </Flex>
  )
}

function counted(values: string[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1)
  return counts
}

/**
 * 模板页上面的筛选：搜索框 + 功能领域、国家和地区、自定义标签三排可以点的标签；
 * 我的模板再加来源、排序。点哪个领域、哪个国家，下面只显示那一部分。
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
  /** 我的模板：多出自定义标签、来源、排序。 */
  mine: boolean
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch })
  const toggle = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
  const domains = [...counted(templates.flatMap(domainsOf))].map(([v, n]) => ({ value: v, text: v, count: n }))
  const general = templates.filter((t) => !(t.countries ?? []).length).length
  const countries = [
    ...(general ? [{ value: '', text: '通用', count: general }] : []),
    ...[...counted(templates.flatMap((t) => t.countries ?? []))].map(([v, n]) => ({
      value: v,
      text: countryName(v),
      count: n,
    })),
  ]
  const tags = [...counted(templates.flatMap((t) => t.tags ?? []))].map(([v, n]) => ({
    value: v,
    text: `#${v}`,
    count: n,
  }))
  const sources = [...counted(templates.map((t) => sourceKind(t.source)))]
  return (
    <Flex vertical gap={8}>
      <Flex gap={8} wrap align="center">
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="按名字、说明找"
          value={value.q}
          onChange={(e) => set({ q: e.target.value })}
          style={{ width: 240 }}
        />
        {mine && sources.length > 1 && (
          <Select<string>
            allowClear
            placeholder="全部来源"
            value={value.source}
            onChange={(source) => set({ source })}
            options={sources.map(([v, n]) => ({ value: v, label: `${SOURCE_TEXT[v] ?? v}（${n}）` }))}
            style={{ width: 170 }}
          />
        )}
        {mine && (
          <Select<Filters['sort']>
            value={value.sort}
            onChange={(sort) => set({ sort })}
            options={[
              { value: 'updated', label: '最近修改的在前' },
              { value: 'used_recent', label: '最近用过的在前' },
              { value: 'used_most', label: '用得最多的在前' },
              { value: 'name', label: '按名字' },
            ]}
            style={{ width: 160 }}
          />
        )}
      </Flex>
      <ChipRow
        label="功能领域"
        items={domains}
        selected={value.domains}
        onToggle={(v) => set({ domains: toggle(value.domains, v) })}
        onClear={() => set({ domains: [] })}
      />
      <ChipRow
        label="国家和地区"
        items={countries}
        selected={value.country === undefined ? [] : [value.country]}
        onToggle={(v) => set({ country: value.country === v ? undefined : v })}
        onClear={() => set({ country: undefined })}
      />
      {mine && (
        <ChipRow
          label="自定义标签"
          items={tags}
          selected={value.tags}
          onToggle={(v) => set({ tags: toggle(value.tags, v) })}
          onClear={() => set({ tags: [] })}
        />
      )}
    </Flex>
  )
}
