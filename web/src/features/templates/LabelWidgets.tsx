/** 模板标签的输入和显示（ADR 0037）。 */

import { Flex, Select, Tag, Typography } from 'antd'
import type { CSSProperties, ReactNode } from 'react'
import type { Template } from '../../api/types'
import { countryName, countryOptions, DOMAIN_COLORS, GENERAL, useLabelVocabulary } from './labels'

/**
 * 选国家和地区：可以选好几个；"通用"和国家二选一——选了通用就清空国家，选了国家就不再是通用。
 * value 是国家代码列表，空 = 通用。
 */
export function CountrySelect({
  value,
  onChange,
  disabled,
  style,
}: {
  value: string[]
  onChange: (countries: string[]) => void
  disabled?: boolean
  style?: CSSProperties
}) {
  const shown = value.length === 0 ? [GENERAL] : value
  return (
    <Select
      mode="multiple"
      value={shown}
      disabled={disabled}
      options={countryOptions()}
      showSearch={{ optionFilterProp: 'label' }}
      placeholder="通用"
      style={style}
      onChange={(next: string[]) => {
        const added = next.find((c) => !shown.includes(c))
        if (added === GENERAL || next.length === 0) onChange([])
        else onChange(next.filter((c) => c !== GENERAL))
      }}
    />
  )
}

/** 卡片、标题上的标签：领域（有颜色）、国家（蓝色，没有就是"通用"）、自定义（灰色 #）。 */
export function LabelTags({ template, custom = true }: { template: Template; custom?: boolean }) {
  const domains = template.domains?.length ? template.domains : template.category ? [template.category] : []
  const countries = template.countries ?? []
  return (
    <>
      {domains.map((d) => (
        <Tag key={`d-${d}`} color={DOMAIN_COLORS[d] ?? 'default'} style={{ marginInlineEnd: 4 }}>
          {d}
        </Tag>
      ))}
      {countries.length === 0 ? (
        <Tag style={{ marginInlineEnd: 4 }}>通用</Tag>
      ) : (
        countries.map((c) => (
          <Tag key={`c-${c}`} color="geekblue" style={{ marginInlineEnd: 4 }}>
            {countryName(c)}
          </Tag>
        ))
      )}
      {custom &&
        (template.tags ?? []).map((t) => (
          <Tag key={`t-${t}`} bordered={false} style={{ marginInlineEnd: 4 }}>
            #{t}
          </Tag>
        ))}
    </>
  )
}

/** 编辑标签的三个框：功能领域（可以多选、可以自己写）、国家和地区（或通用）、自定义标签。 */
export function LabelFields({
  domains,
  countries,
  tags,
  onChange,
  disabled,
}: {
  domains: string[]
  countries: string[]
  tags: string[]
  onChange: (patch: { domains?: string[]; countries?: string[]; tags?: string[] }) => void
  disabled?: boolean
}) {
  const vocabulary = useLabelVocabulary()
  const row = (label: string, input: ReactNode) => (
    <Flex align="center" gap={8} wrap>
      <Typography.Text style={{ width: 84, flexShrink: 0 }}>{label}</Typography.Text>
      <div style={{ flex: 1, minWidth: 220 }}>{input}</div>
    </Flex>
  )
  return (
    <Flex vertical gap={8}>
      {row(
        '功能领域：',
        <Select
          mode="tags"
          value={domains}
          disabled={disabled}
          placeholder="选或者输入后回车，如 发票、交通"
          options={vocabulary.domains.map((d) => ({ value: d, label: d }))}
          onChange={(next: string[]) => onChange({ domains: next })}
          style={{ width: '100%' }}
        />,
      )}
      {row(
        '国家和地区：',
        <CountrySelect
          value={countries}
          disabled={disabled}
          onChange={(next) => onChange({ countries: next })}
          style={{ width: '100%' }}
        />,
      )}
      {row(
        '自定义标签：',
        <Select
          mode="tags"
          value={tags}
          disabled={disabled}
          placeholder="自己起名字，回车添加，如 2026-10 出差"
          options={vocabulary.tags.map((t) => ({ value: t, label: t }))}
          onChange={(next: string[]) => onChange({ tags: next })}
          style={{ width: '100%' }}
        />,
      )}
    </Flex>
  )
}
