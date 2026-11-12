import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Flex, Input, Select, Switch, Table, type TableProps, Tooltip } from 'antd'
import type { TemplateField } from '../../api/types'
import { blankField, FIELD_ID, TYPE_OPTIONS } from './templateFields'

/**
 * 字段（或表格的列）编辑表：中文名、字段 ID、类型、必填、导出、敏感、别名、给模型的说明。
 * locked：已经存过的字段 ID 不能改（旧文档、规则、导出都靠它对齐）。
 */
export function FieldsEditor({
  fields,
  onChange,
  locked,
  readOnly,
}: {
  fields: TemplateField[]
  onChange: (fields: TemplateField[]) => void
  locked: Set<string>
  readOnly: boolean
}) {
  const update = (i: number, patch: Partial<TemplateField>) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const move = (i: number, by: number) => {
    const next = [...fields]
    const [item] = next.splice(i, 1)
    next.splice(i + by, 0, item)
    onChange(next)
  }
  const duplicate = (name: string) => fields.filter((f) => f.name === name).length > 1

  const columns: TableProps<TemplateField>['columns'] = [
    {
      title: '中文名',
      dataIndex: 'label',
      width: 150,
      render: (_, f, i) => (
        <Input
          value={f.label}
          disabled={readOnly}
          status={f.label.trim() ? undefined : 'error'}
          placeholder="例如：总额"
          onChange={(e) => update(i, { label: e.target.value })}
        />
      ),
    },
    {
      title: (
        <Tooltip title="小写英文字母开头，只有小写字母、数字、下划线。存过之后不能改：旧文档、规则、导出都靠它对齐">
          字段 ID
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
      title: '类型',
      dataIndex: 'type',
      width: 130,
      render: (_, f, i) => (
        <Select
          value={f.type}
          disabled={readOnly}
          options={TYPE_OPTIONS}
          onChange={(type) => update(i, { type })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '必填',
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
        <Tooltip title="关掉 = 人工填（报销人、部门……）：不交给模型抽，审核时自己填">
          从单据抽
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
      title: <Tooltip title="导出 Excel 时要不要这一列">导出</Tooltip>,
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
      title: <Tooltip title="证件号码这类敏感信息：打开后默认不导出">敏感</Tooltip>,
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
      title: <Tooltip title="单据上的其他写法（Check-in、入住日期……），回车添加，会一起告诉模型">别名</Tooltip>,
      dataIndex: 'aliases',
      width: 200,
      render: (_, f, i) => (
        <Select
          mode="tags"
          value={f.aliases}
          disabled={readOnly}
          open={false}
          suffixIcon={null}
          placeholder="回车添加"
          onChange={(aliases: string[]) => update(i, { aliases })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: (
        <Tooltip title="只能填这几个值（如费用类别）：回车添加。抽到别的值会提醒；不写 = 不限">
          可选值
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
          placeholder="不限"
          onChange={(options: string[]) => update(i, { options })}
          style={{ width: '100%' }}
        />
      ),
    },
    {
      title: '给模型的说明',
      dataIndex: 'description',
      render: (_, f, i) => (
        <Input.TextArea
          value={f.description}
          disabled={readOnly}
          autoSize={{ minRows: 1, maxRows: 4 }}
          placeholder="在单据哪里、长什么样、别和什么搞混"
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
                  aria-label="上移"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                />
                <Button
                  size="small"
                  type="text"
                  icon={<ArrowDownOutlined />}
                  aria-label="下移"
                  disabled={i === fields.length - 1}
                  onClick={() => move(i, 1)}
                />
                <Tooltip title={`删掉「${f.label || f.name}」`}>
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    aria-label="删除"
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
          加一个字段
        </Button>
      )}
    </Flex>
  )
}
