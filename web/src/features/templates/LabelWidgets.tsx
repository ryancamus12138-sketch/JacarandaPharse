/** Input and display of template tags. */

import { Flex, Select, Tag, Typography } from 'antd'
import type { CSSProperties, ReactNode } from 'react'
import type { Template } from '../../api/types'
import { countryName, countryOptions, DOMAIN_COLORS, domainPairs, GENERAL, useLabelVocabulary } from './labels'
import { useTranslation } from 'react-i18next'

/**
 * Choose countries and regions: several may be selected; "generic" and countries are mutually exclusive
 * (choosing generic clears the countries, choosing a country clears generic).
 * value is a list of country codes; empty means generic.
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
  const { t } = useTranslation()
  const shown = value.length === 0 ? [GENERAL] : value
  return (
    <Select
      mode="multiple"
      value={shown}
      disabled={disabled}
      options={countryOptions()}
      showSearch={{ optionFilterProp: 'label' }}
      placeholder={t('通用')}
      style={style}
      onChange={(next: string[]) => {
        const added = next.find((c) => !shown.includes(c))
        if (added === GENERAL || next.length === 0) onChange([])
        else onChange(next.filter((c) => c !== GENERAL))
      }}
    />
  )
}

/**
 * Tags on cards and titles: domains (colored), countries (blue, "generic" when none), custom tags (grey #).
 */
export function LabelTags({ template, custom = true }: { template: Template; custom?: boolean }) {
  const { t } = useTranslation()
  const countries = template.countries ?? []
  // Names in the interface language: known and used domains first (also right for domains being edited, which
  // the template's own names do not cover yet).
  const names = new Map(useLabelVocabulary().domains.map((d) => [d.value, d.label]))
  return (
    <>
      {domainPairs(template).map((d) => (
        <Tag key={`d-${d.value}`} color={DOMAIN_COLORS[d.value] ?? 'default'} style={{ marginInlineEnd: 4 }}>
          {names.get(d.value) ?? d.label}
        </Tag>
      ))}
      {countries.length === 0 ? (
        <Tag style={{ marginInlineEnd: 4 }}>{t('通用')}</Tag>
      ) : (
        countries.map((c) => (
          <Tag key={`c-${c}`} color="geekblue" style={{ marginInlineEnd: 4 }}>
            {countryName(c)}
          </Tag>
        ))
      )}
      {custom &&
        (template.tags ?? []).map((tag) => (
          <Tag key={`t-${tag}`} bordered={false} style={{ marginInlineEnd: 4 }}>
            #{tag}
          </Tag>
        ))}
    </>
  )
}

/**
 * Three tag inputs: domains (multiple, free text allowed), countries and regions (or generic), custom tags.
 */
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
  const { t } = useTranslation()
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
        t('功能领域：'),
        <Select
          mode="tags"
          value={domains}
          disabled={disabled}
          placeholder={t('选择或输入后按回车，如“发票”“交通”')}
          // Known domains are shown in the interface language; what is saved is their stored form.
          options={vocabulary.domains}
          optionFilterProp="label"
          onChange={(next: string[]) => onChange({ domains: next })}
          style={{ width: '100%' }}
        />,
      )}
      {row(
        t('国家和地区：'),
        <CountrySelect
          value={countries}
          disabled={disabled}
          onChange={(next) => onChange({ countries: next })}
          style={{ width: '100%' }}
        />,
      )}
      {row(
        t('自定义标签：'),
        <Select
          mode="tags"
          value={tags}
          disabled={disabled}
          placeholder={t('自定义名称，按回车添加，如“2026-10 出差”')}
          options={vocabulary.tags.map((tag) => ({ value: tag, label: tag }))}
          onChange={(next: string[]) => onChange({ tags: next })}
          style={{ width: '100%' }}
        />,
      )}
    </Flex>
  )
}
