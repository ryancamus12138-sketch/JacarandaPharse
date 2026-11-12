import { DeleteOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Checkbox,
  Drawer,
  Flex,
  Input,
  Select,
  Switch,
  Table,
  Typography,
} from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { createFromSample } from '../../api/templates'
import type { DocumentDetail, FieldType, SampleField } from '../../api/types'
import {
  blankField,
  CATEGORIES,
  FIELD_ID,
  fieldsProblem,
  TYPE_OPTIONS,
} from '../templates/templateFields'

interface Row extends SampleField {
  include: boolean
  /** 单据上的原名（自己加的字段为空）。 */
  original: string
  sample: string | null
}

/** 从单据上的名称猜一个字段 ID：英文就用它（Invoice No. → invoice_no），中文就编号。 */
function guessId(label: string, used: Set<string>, n: number): string {
  let id = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
  if (!FIELD_ID.test(id)) id = `field_${n}`
  let candidate = id
  for (let i = 2; used.has(candidate); i++) candidate = `${id}_${i}`
  used.add(candidate)
  return candidate
}

function initialRows(doc: DocumentDetail): Row[] {
  const used = new Set<string>()
  const first = doc.extractions[0]?.values ?? []
  return first.map((v, i) => ({
    ...blankField([]),
    source: v.name,
    include: true,
    original: v.label ?? v.name,
    sample: v.current_value,
    label: v.label ?? v.name,
    name: guessId(v.label ?? '', used, i + 1),
    type: v.type as FieldType,
  }))
}

/**
 * 用样例建模板（ADR 0030）：这份文档抽出了所有键值对，勾选要的、起好中文名和字段 ID，
 * 还可以加单据上没有、要自己填的字段。保存后这份文档直接换成按新模板的结果。
 */
export function SaveAsTemplate({
  doc,
  open,
  onClose,
}: {
  doc: DocumentDetail
  open: boolean
  onClose: () => void
}) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [rows, setRows] = useState<Row[]>(() => initialRows(doc))
  const [title, setTitle] = useState(doc.filename.replace(/\.[^.]+$/, ''))
  const [category, setCategory] = useState('其他')
  const [description, setDescription] = useState('')
  const update = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const chosen = rows.filter((r) => r.include)
  const problem =
    (!title.trim() ? '请给模板起个名字' : null) ??
    (chosen.length === 0 ? '至少选一个字段' : null) ??
    fieldsProblem(chosen, '')

  const save = useMutation({
    mutationFn: () =>
      createFromSample({
        document_id: doc.id,
        title: title.trim(),
        category,
        description,
        rules: [],
        fields: chosen.map(({ include: _i, original: _o, sample: _s, ...f }) => f),
      }),
    onSuccess: ({ template }) => {
      void queryClient.invalidateQueries({ queryKey: ['templates'] })
      void queryClient.invalidateQueries({ queryKey: ['document', doc.id] })
      void message.success(`已存成模板「${template.title}」，这份文档已换成按新模板的结果`)
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={980}
      title="存成模板"
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          disabled={problem !== null}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          保存模板（{chosen.length} 个字段）
        </Button>
      }
    >
      <Flex vertical gap={12}>
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          选中的值原样搬过去（值、出处、你改过的都在），不重新抽取。单据上的原名会记成别名，
          以后同类单据模型更容易认出来。规则、表格以后在"模板"页里加。
        </Typography.Paragraph>
        {problem && <Alert type="warning" showIcon title={problem} />}
        <Flex gap={8} wrap>
          <Input addonBefore="模板名" value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 300 }} />
          <AutoComplete
            value={category}
            options={CATEGORIES.map((c) => ({ value: c }))}
            onChange={setCategory}
            style={{ width: 140 }}
          />
          <Input
            placeholder="说明（可以不写）"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={{ flex: 1, minWidth: 200 }}
          />
        </Flex>
        <Table<Row>
          size="small"
          rowKey={(_, i) => String(i)}
          pagination={false}
          dataSource={rows}
          scroll={{ x: 900 }}
          columns={[
            {
              title: '要',
              width: 48,
              render: (_, r, i) => <Checkbox checked={r.include} onChange={(e) => update(i, { include: e.target.checked })} />,
            },
            {
              title: '单据上的名称 / 值',
              width: 220,
              render: (_, r) =>
                r.source ? (
                  <Flex vertical>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {r.original}
                    </Typography.Text>
                    <Typography.Text ellipsis style={{ maxWidth: 200 }}>
                      {r.sample ?? '（空）'}
                    </Typography.Text>
                  </Flex>
                ) : (
                  <Typography.Text type="secondary">自己加的（空，审核时填）</Typography.Text>
                ),
            },
            {
              title: '中文名',
              width: 160,
              render: (_, r, i) => (
                <Input value={r.label} disabled={!r.include} onChange={(e) => update(i, { label: e.target.value })} />
              ),
            },
            {
              title: '字段 ID',
              width: 160,
              render: (_, r, i) => (
                <Input
                  value={r.name}
                  disabled={!r.include}
                  status={!r.include || FIELD_ID.test(r.name) ? undefined : 'error'}
                  onChange={(e) => update(i, { name: e.target.value.trim() })}
                />
              ),
            },
            {
              title: '类型',
              width: 130,
              render: (_, r, i) => (
                <Select
                  value={r.type}
                  disabled={!r.include}
                  options={TYPE_OPTIONS}
                  onChange={(type) => update(i, { type })}
                  style={{ width: '100%' }}
                />
              ),
            },
            {
              title: '必填',
              width: 60,
              render: (_, r, i) => (
                <Switch size="small" checked={r.required} disabled={!r.include} onChange={(required) => update(i, { required })} />
              ),
            },
            {
              title: '敏感',
              width: 60,
              render: (_, r, i) => (
                <Switch
                  size="small"
                  checked={r.sensitive}
                  disabled={!r.include}
                  onChange={(sensitive) => update(i, { sensitive, export: !sensitive })}
                />
              ),
            },
            {
              title: '',
              width: 48,
              render: (_, r, i) =>
                r.source ? null : (
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    aria-label="删除"
                    onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                  />
                ),
            },
          ]}
        />
        <Button
          icon={<PlusOutlined />}
          style={{ alignSelf: 'start' }}
          onClick={() =>
            setRows((rs) => [
              ...rs,
              { ...blankField(rs), source: null, include: true, original: '', sample: null },
            ])
          }
        >
          加一个自己的字段
        </Button>
      </Flex>
    </Drawer>
  )
}
