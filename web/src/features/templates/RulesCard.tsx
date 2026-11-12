import { DeleteOutlined, FunctionOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Card, Flex, Input, List, Select, Switch, Tag, Typography } from 'antd'
import { useState } from 'react'
import { FormulaInput } from './FormulaInput'
import { countryName } from './labels'
import type { CustomRule, RuleInfo, Template, TemplateIn } from '../../api/types'



/** 规则要用的字段这个模板里有没有：没有的话这条规则永远跳过。 */
function missingFor(rule: RuleInfo, draft: TemplateIn): string[] {
  const names = new Set(draft.fields.map((f) => f.name))
  for (const t of draft.tables) {
    names.add(t.name)
    for (const c of t.columns) names.add(`${t.name}.*.${c.name}`)
  }
  if (rule.per_row) {
    const table = draft.tables.find((t) => t.name === rule.per_row)
    if (!table) return [rule.per_row]
    return rule.needs.filter((n) => !table.columns.some((c) => c.name === n))
  }
  return rule.needs.filter((n) => !names.has(n))
}

/**
 * 模板的规则：内置规则（开关）+ 自己写的规则（可以加、可以删，ADR 0035）。
 * 只列这个模板用得上的规则（要的字段模板里都有）：通用模板只列通用规则；
 * 中国、澳洲的模板（从地区起步模板复制的）再加本地区的规则。别的检查自己写。
 */
export function RulesCard({
  template,
  draft,
  set,
  rules,
  loading,
  readOnly,
}: {
  template: Template
  draft: TemplateIn
  set: (patch: Partial<TemplateIn>) => void
  rules: RuleInfo[]
  loading: boolean
  readOnly: boolean
}) {
  // 选了的在前，其次是这个模板能用的（字段都有），最后是缺字段的
  const groups = rules
    .map((rule) => ({
      rule,
      missing: missingFor(rule, draft),
      saved: template.rules.some((r) => r.id === rule.id),
      on: draft.rules.includes(rule.id),
    }))
    .sort((a, b) => Number(b.saved) - Number(a.saved) || a.missing.length - b.missing.length)
  // 默认只列用得上的：要的字段都有，而且是通用规则或本模板所属国家的规则。
  // 我的模板不限制（ADR 0037）：点"显示全部"可以选别的国家的规则、缺字段的规则
  const [showAll, setShowAll] = useState(false)
  const countries = draft.countries ?? []
  const usable = (g: (typeof groups)[number]) =>
    g.missing.length === 0 && (!g.rule.country || countries.includes(g.rule.country))
  const shown = groups.filter((g) => (readOnly ? g.on : showAll || g.on || g.saved || usable(g)))
  const hidden = readOnly ? 0 : groups.length - groups.filter((g) => g.on || g.saved || usable(g)).length
  const custom = draft.custom_rules
  const setCustom = (i: number, patch: Partial<CustomRule>) =>
    set({ custom_rules: custom.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

  return (
    <Card title={`规则（选了 ${draft.rules.length + custom.length} 条）`} size="small">
      <Typography.Paragraph type="secondary">
        规则用单据上的数字互相验证（例如 明细合计 = 总额）。默认只列这个模板用得上的：通用规则，
        加上这个模板所属国家的规则；别的国家的规则、缺字段的规则点下面"显示全部"也能选。
        别的检查在"自己写的规则"里写。必填字段没值也会标红。
      </Typography.Paragraph>
      <List
        size="small"
        loading={loading}
        dataSource={shown}
        renderItem={({ rule, missing, on }) => (
          <List.Item>
            <Flex gap={8} align="center" wrap style={{ width: '100%' }}>
              <Switch
                size="small"
                checked={on}
                disabled={readOnly}
                onChange={(checked) =>
                  set({
                    rules: checked ? [...draft.rules, rule.id] : draft.rules.filter((r) => r !== rule.id),
                  })
                }
              />
              <Typography.Text>{rule.title}</Typography.Text>
              <Tag color={rule.severity === 'error' ? 'red' : 'gold'}>
                {rule.severity === 'error' ? '错误' : '警告'}
              </Tag>
              {rule.country && <Tag color="geekblue">{countryName(rule.country)}</Tag>}
              {missing.length > 0 && (
                <Typography.Text type={on ? 'warning' : 'secondary'} style={{ fontSize: 12 }}>
                  缺字段 {missing.join('、')}
                  {on ? '，会跳过' : ''}
                </Typography.Text>
              )}
            </Flex>
          </List.Item>
        )}
      />
      {!readOnly && (hidden > 0 || showAll) && (
        <Button type="link" size="small" onClick={() => setShowAll(!showAll)} style={{ paddingInline: 0 }}>
          {showAll ? '只看用得上的' : `显示全部（还有 ${hidden} 条：别的国家的、缺字段的）`}
        </Button>
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        自己写的规则
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        点条件输入框右边的 <FunctionOutlined /> 按钮，会列出这个模板现在有哪些字段（中文名和字段 ID）、
        表格合计和比较符号，点一下就插进去；写的时候下面会用中文显示条件的意思，写错了会说哪里错。
        例如 <Typography.Text code>sum(items.amount) == total</Typography.Text>（明细金额合计 = 总额）、
        <Typography.Text code>total &lt;= 5000</Typography.Text>（总额不超过 5000）。
        选"每一行"时每行单独检查，按钮里列的是那张表的列。金额相等允许差半分钱。
      </Typography.Paragraph>
      {custom.length === 0 && readOnly && <Typography.Text type="secondary">没有</Typography.Text>}
      <Flex vertical gap={8}>
        {custom.map((c, i) => (
          <Flex key={i} gap={8} align="center" wrap>
            <Input
              value={c.title}
              disabled={readOnly}
              placeholder="名字（可以不写）"
              onChange={(e) => setCustom(i, { title: e.target.value })}
              style={{ width: 200 }}
            />
            <div style={{ flex: 1, minWidth: 260 }}>
              <FormulaInput
                value={c.check}
                disabled={readOnly}
                kind="condition"
                fields={draft.fields}
                tables={draft.tables}
                row={c.per_row}
                placeholder="条件：点右边的按钮选字段，例如 明细金额合计 == 总额"
                onChange={(check) => setCustom(i, { check })}
              />
            </div>
            <Select
              value={c.per_row ?? ''}
              disabled={readOnly}
              onChange={(v: string) => setCustom(i, { per_row: v || null })}
              options={[
                { value: '', label: '整张单据' },
                ...draft.tables.map((t) => ({ value: t.name, label: `${t.label}的每一行` })),
              ]}
              style={{ width: 160 }}
            />
            <Select
              value={c.severity}
              disabled={readOnly}
              onChange={(severity: CustomRule['severity']) => setCustom(i, { severity })}
              options={[
                { value: 'error', label: '错误' },
                { value: 'warning', label: '警告' },
              ]}
              style={{ width: 90 }}
            />
            {!readOnly && (
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                aria-label="删掉这条规则"
                onClick={() => set({ custom_rules: custom.filter((_, j) => j !== i) })}
              />
            )}
          </Flex>
        ))}
      </Flex>
      {!readOnly && (
        <Button
          icon={<PlusOutlined />}
          style={{ marginTop: 8 }}
          onClick={() =>
            set({ custom_rules: [...custom, { title: '', check: '', severity: 'error', per_row: null }] })
          }
        >
          加一条规则
        </Button>
      )}
    </Card>
  )
}
