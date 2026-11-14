import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Flex, Input, Select, Switch, Table, type TableProps, Tooltip } from 'antd'
import type { TemplateField, TemplateTable } from '../../api/types'
import { languageName } from '../../utils/languages'
import { FormulaInput } from './FormulaInput'
import { blankField, FIELD_ID, RANGE_EXPORTS, TYPE_OPTIONS } from './templateFields'
import { useTranslation } from 'react-i18next'

/**
 * Editor table for fields (or table columns): display name, field ID, type, required, export, sensitive,
 * aliases, description for the model.
 * locked: field IDs already saved cannot change (older documents, rules and exports rely on them).
 */
export function FieldsEditor({
  fields,
  onChange,
  locked,
  readOnly,
  scope,
  language = 'zh',
  primary = 'zh',
}: {
  /**
   * Language of the names and descriptions being edited: the primary language edits label and description;
   * other languages edit the corresponding entries in names and descriptions.
   */
  language?: string
  /** The template's primary language. */
  primary?: string
  fields: TemplateField[]
  onChange: (fields: TemplateField[]) => void
  locked: Set<string>
  readOnly: boolean
  /**
   * Fields usable in formulas: all fields and tables of the template; row is the table containing these
   * columns (null for regular fields).
   */
  scope: { fields: TemplateField[]; tables: TemplateTable[]; row: string | null }
}) {
  const { t } = useTranslation()
  const update = (i: number, patch: Partial<TemplateField>) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const move = (i: number, by: number) => {
    const next = [...fields]
    const [item] = next.splice(i, 1)
    next.splice(i + by, 0, item)
    onChange(next)
  }
  const duplicate = (name: string) => fields.filter((f) => f.name === name).length > 1

  const other = language !== primary
  const columns: TableProps<TemplateField>['columns'] = [
    {
      title: other
        ? t('{{languageName}} 名称', { languageName: languageName(language) })
        : t('{{languageName}} 名称（主语言）', { languageName: languageName(language) }),
      dataIndex: 'label',
      width: 150,
      render: (_, f, i) =>
        other ? (
          // Other languages: empty values fall back to the default name for export and display, so they may
          // stay empty.
          <Input
            value={f.names?.[language] ?? ''}
            disabled={readOnly}
            placeholder={f.label}
            onChange={(e) => update(i, { names: { ...f.names, [language]: e.target.value } })}
          />
        ) : (
          <Input
            value={f.label}
            disabled={readOnly}
            status={f.label.trim() ? undefined : 'error'}
            placeholder={t('例如：总额')}
            onChange={(e) => update(i, { label: e.target.value })}
          />
        ),
    },
    {
      title: (
        <Tooltip title={t('小写英文字母开头，只有小写字母、数字、下划线。存过之后不能改：旧文档、规则、导出都靠它对齐')}>
          {t('字段 ID')}
        </Tooltip>
      ),
      dataIndex: 'name',
      width: 150,
      render: (_, f, i) => (
        <Input
          value={f.name}
          disabled={readOnly || locked.has(f.name)}
          status={FIELD_ID.test(f.name) && !duplicate(f.name) ? undefined : 'error'}
          onChange={(e) => update(i, { name: e.target.value.trim() })}
        />
      ),
    },
    {
      title: (
        <Tooltip title={t('日期范围（2014-07-01 ~ 2014-07-31 这种）：模型照抄原文，系统拆成开始、结束；导出时可以选统一格式、原文或拆成两列')}>
          {t('类型')}
        </Tooltip>
      ),
      dataIndex: 'type',
      width: 150,
      render: (_, f, i) => (
        <Flex vertical gap={4}>
          <Select
            value={f.type}
            disabled={readOnly}
            options={TYPE_OPTIONS}
            onChange={(type) => update(i, { type })}
            style={{ width: '100%' }}
          />
          {f.type === 'date_range' && (
            <>
              <Select
                size="small"
                value={f.range_export ?? 'normalized'}
                disabled={readOnly}
                options={RANGE_EXPORTS.map((o) => ({ ...o, label: t('导出：{{label}}', { label: o.label }) }))}
                onChange={(range_export) => update(i, { range_export })}
              />
              {(f.range_export ?? 'normalized') === 'normalized' && (
                <Input
                  size="small"
                  addonBefore={t('分隔')}
                  value={f.range_separator ?? ' ~ '}
                  disabled={readOnly}
                  maxLength={10}
                  onChange={(e) => update(i, { range_separator: e.target.value || ' ~ ' })}
                />
              )}
            </>
          )}
        </Flex>
      ),
    },
    {
      title: t('必填'),
      dataIndex: 'required',
      width: 60,
      render: (_, f, i) => (
        <Switch
          size="small"
          checked={f.required}
          disabled={readOnly}
          onChange={(required) => update(i, { required })}
        />
      ),
    },
    {
      title: (
        <Tooltip title={t('关掉 = 人工填（报销人、部门……）：不交给模型抽，审核时自己填')}>
          {t('从单据抽')}
        </Tooltip>
      ),
      dataIndex: 'extract',
      width: 80,
      render: (_, f, i) => (
        <Switch
          size="small"
          checked={f.extract}
          disabled={readOnly}
          onChange={(extract) => update(i, { extract })}
        />
      ),
    },
    {
      title: (
        <Tooltip title={t('按别的字段算出来，不让模型去单据上找。点输入框右边的按钮选字段，例如 离店日期 − 入住日期、数量 × 单价、明细金额合计。只能是数字字段')}>
          {t('公式')}
        </Tooltip>
      ),
      dataIndex: 'compute',
      width: 240,
      render: (_, f, i) => (
        <FormulaInput
          value={f.compute ?? ''}
          disabled={readOnly}
          kind="formula"
          fields={scope.fields}
          tables={scope.tables}
          row={scope.row}
          self={f.name}
          placeholder={t('不算')}
          onChange={(compute) => update(i, { compute, ...(compute.trim() ? { type: 'number' as const } : {}) })}
        />
      ),
    },
    {
      title: (
        <Tooltip title={t('发票号码、账单号这类：别的单据里有一样的值，就标黄提醒“可能重复报销”')}>
          {t('不能重复')}
        </Tooltip>
      ),
      dataIndex: 'unique',
      width: 80,
      render: (_, f, i) => (
        <Switch
          size="small"
          checked={f.unique ?? false}
          disabled={readOnly || scope.row !== null}
          onChange={(unique) => update(i, { unique })}
        />
      ),
    },
    {
      title: <Tooltip title={t('导出 Excel 时要不要这一列')}>{t('导出')}</Tooltip>,
      dataIndex: 'export',
      width: 60,
      render: (_, f, i) => (
        <Switch
          size="small"
          checked={f.export}
          disabled={readOnly}
          onChange={(on) => update(i, { export: on })}
        />
      ),
    },
    {
      title: <Tooltip title={t('证件号码这类敏感信息：打开后默认不导出')}>{t('敏感')}</Tooltip>,
      dataIndex: 'sensitive',
      width: 60,
      render: (_, f, i) => (
        <Switch
          size="small"
          checked={f.sensitive}
          disabled={readOnly}
          onChange={(sensitive) => update(i, sensitive ? { sensitive, export: false } : { sensitive })}
        />
      ),
    },
    {
      title: <Tooltip title={t('单据上的其他写法（Check-in、入住日期……），回车添加，会一起告诉模型')}>{t('别名')}</Tooltip>,
      dataIndex: 'aliases',
      width: 200,
      render: (_, f, i) => (
        <Select
          mode="tags"
          value={f.aliases}
          disabled={readOnly}
          open={false}
          suffixIcon={null}
          placeholder={t('回车添加')}
          onChange={(aliases: string[]) => update(i, { aliases })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: (
        <Tooltip title={t('只能填这几个值（如费用类别）：回车添加。抽到别的值会提醒；不写 = 不限')}>
          {t('可选值')}
        </Tooltip>
      ),
      dataIndex: 'options',
      width: 180,
      render: (_, f, i) => (
        <Select
          mode="tags"
          value={f.options}
          disabled={readOnly}
          open={false}
          suffixIcon={null}
          placeholder={t('不限')}
          onChange={(options: string[]) => update(i, { options })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: other
        ? t('说明（{{languageName}}）', { languageName: languageName(language) })
        : t('给模型的说明'),
      dataIndex: 'description',
      render: (_, f, i) =>
        other ? (
          // Other languages are for reading (previews); the model gets the primary language description.
          <Input.TextArea
            value={f.descriptions?.[language] ?? ''}
            disabled={readOnly}
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder={f.description}
            onChange={(e) => update(i, { descriptions: { ...f.descriptions, [language]: e.target.value } })}
          />
        ) : (
          <Input.TextArea
            value={f.description}
            disabled={readOnly}
            autoSize={{ minRows: 1, maxRows: 4 }}
            placeholder={t('在单据哪里、长什么样、别和什么搞混')}
            onChange={(e) => update(i, { description: e.target.value })}
          />
        ),
    },
    ...(readOnly
      ? []
      : [
          {
            title: '',
            key: 'actions',
            width: 110,
            render: (_: unknown, f: TemplateField, i: number) => (
              <Flex gap={2}>
                <Button
                  size="small"
                  type="text"
                  icon={<ArrowUpOutlined />}
                  aria-label={t('上移')}
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                />
                <Button
                  size="small"
                  type="text"
                  icon={<ArrowDownOutlined />}
                  aria-label={t('下移')}
                  disabled={i === fields.length - 1}
                  onClick={() => move(i, 1)}
                />
                <Tooltip title={t('删掉「{{value}}」', { value: f.label || f.name })}>
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    aria-label={t('删除')}
                    onClick={() => onChange(fields.filter((_, j) => j !== i))}
                  />
                </Tooltip>
              </Flex>
            ),
          },
        ]),
  ]

  return (
    <Flex vertical gap={8}>
      <Table<TemplateField>
        size="small"
        rowKey={(_, i) => String(i)}
        columns={columns}
        dataSource={fields}
        pagination={false}
        scroll={{ x: 1400 }}
      />
      {!readOnly && (
        <Button
          icon={<PlusOutlined />}
          onClick={() => onChange([...fields, blankField(fields)])}
          style={{ alignSelf: 'start' }}
        >
          {t('加一个字段')}
        </Button>
      )}
    </Flex>
  )
}
