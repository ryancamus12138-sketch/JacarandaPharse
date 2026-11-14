import { DeleteOutlined, FunctionOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Card, Flex, Input, List, Select, Switch, Tag, Typography } from 'antd'
import { FormulaInput } from './FormulaInput'
import { countryName } from './labels'
import type { CustomRule, RuleInfo, Template, TemplateIn } from '../../api/types'
import { useTranslation } from 'react-i18next'



/** Whether the template has the fields a rule needs; otherwise the rule is always skipped. */
function missingFor(rule: RuleInfo, draft: TemplateIn): string[] {
  const names = new Set(draft.fields.map((f) => f.name))
  for (const tbl of draft.tables) {
    names.add(tbl.name)
    for (const c of tbl.columns) names.add(`${tbl.name}.*.${c.name}`)
  }
  const wanted = [...rule.needs, ...(rule.requires ?? [])]
  if (rule.per_row) {
    const table = draft.tables.find((tbl) => tbl.name === rule.per_row)
    if (!table) return [rule.per_row]
    return wanted.filter((n) => !table.columns.some((c) => c.name === n))
  }
  return wanted.filter((n) => !names.has(n))
}

/**
 * Template rules: built-in rules (switches) + custom rules (addable and removable).
 * Only rules relevant to this template are listed: those already selected, plus generic rules and rules of
 * the template's countries whose fields all exist. Other countries' rules and rules with missing fields are
 * never listed (a lab report shows no invoice or ABN rules). To use a country's rules, select that country
 * under "Countries and regions" above. Write other checks as custom rules.
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
  const { t } = useTranslation()
  // Selected rules first, then usable ones (all fields present), then those missing fields.
  const groups = rules
    .map((rule) => ({
      rule,
      missing: missingFor(rule, draft),
      saved: template.rules.some((r) => r.id === rule.id),
      on: draft.rules.includes(rule.id),
    }))
    .sort((a, b) => Number(b.saved) - Number(a.saved) || a.missing.length - b.missing.length)
  // List only applicable rules: all required fields present, and generic or of the template's countries;
  // rules selected before this edit stay listed so they can be turned off. Read-only views show only selected
  // rules.
  const countries = draft.countries ?? []
  const usable = (g: (typeof groups)[number]) =>
    g.missing.length === 0 && (!g.rule.country || countries.includes(g.rule.country))
  const shown = groups.filter((g) => (readOnly ? g.on : g.on || g.saved || usable(g)))
  const custom = draft.custom_rules
  const setCustom = (i: number, patch: Partial<CustomRule>) =>
    set({ custom_rules: custom.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

  return (
    <Card title={t('规则（选了 {{value}} 条）', { value: draft.rules.length + custom.length })} size="small">
      <Typography.Paragraph type="secondary">
        {t('规则用单据上的数字互相验证（例如 明细合计 = 总额）。这里只列跟这个模板有关的规则： 要用的字段模板里都有的通用规则，和上面选的国家的规则；加了字段、选了国家，相关的规则会出现在这里。 别的检查在"自己写的规则"里写。必填字段没值也会标红。')}
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
                {rule.severity === 'error' ? t('错误') : t('警告')}
              </Tag>
              {rule.country && <Tag color="geekblue">{countryName(rule.country)}</Tag>}
              {missing.length > 0 && (
                <Typography.Text type={on ? 'warning' : 'secondary'} style={{ fontSize: 12 }}>
                  {t('缺字段')}{' '}{missing.join(t('、'))}
                  {on && !readOnly ? t('，保存时会去掉') : ''}
                </Typography.Text>
              )}
              {missing.length === 0 && on && !readOnly && rule.country && !countries.includes(rule.country) && (
                <Typography.Text type="warning" style={{ fontSize: 12 }}>
                  {t('上面没选{{countryName}}，保存时会去掉', { countryName: countryName(rule.country) })}
                </Typography.Text>
              )}
            </Flex>
          </List.Item>
        )}
      />

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        {t('自己写的规则')}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        {t('点条件输入框右边的')}{' '}<FunctionOutlined />{' '}{t('按钮，会列出这个模板现在有哪些字段（中文名和字段 ID）、 表格合计和比较符号，点一下就插进去；写的时候下面会用中文显示条件的意思，写错了会说哪里错。 例如')}{' '}<Typography.Text code>sum(items.amount) == total</Typography.Text>{t('（明细金额合计 = 总额）、')}
        <Typography.Text code>total &lt;= 5000</Typography.Text>{t('（总额不超过 5000）。 选"每一行"时每行单独检查，按钮里列的是那张表的列。金额相等允许差半分钱。')}
      </Typography.Paragraph>
      {custom.length === 0 && readOnly && <Typography.Text type="secondary">{t('没有')}</Typography.Text>}
      <Flex vertical gap={8}>
        {custom.map((c, i) => (
          <Flex key={i} gap={8} align="center" wrap>
            <Input
              value={c.title}
              disabled={readOnly}
              placeholder={t('名字（可以不写）')}
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
                placeholder={t('条件：点右边的按钮选字段，例如 明细金额合计 == 总额')}
                onChange={(check) => setCustom(i, { check })}
              />
            </div>
            <Select
              value={c.per_row ?? ''}
              disabled={readOnly}
              onChange={(v: string) => setCustom(i, { per_row: v || null })}
              options={[
                { value: '', label: t('整张单据') },
                ...draft.tables.map((tbl) => ({ value: tbl.name, label: t('{{label}}的每一行', { label: tbl.label }) })),
              ]}
              style={{ width: 160 }}
            />
            <Select
              value={c.severity}
              disabled={readOnly}
              onChange={(severity: CustomRule['severity']) => setCustom(i, { severity })}
              options={[
                { value: 'error', label: t('错误') },
                { value: 'warning', label: t('警告') },
              ]}
              style={{ width: 90 }}
            />
            {!readOnly && (
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                aria-label={t('删掉这条规则')}
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
          {t('加一条规则')}
        </Button>
      )}
    </Card>
  )
}
