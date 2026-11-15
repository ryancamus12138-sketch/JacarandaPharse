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
import { useTranslation } from 'react-i18next'

interface Row extends SampleField {
  include: boolean
  /** Label on the document (empty for added fields). */
  original: string
  sample: string | null
}

/**
 * Guess a field ID from the label: English labels are converted (Invoice No. → invoice_no); others are
 * numbered.
 */
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
 * Create a template from a sample: this document has all key-value pairs extracted; select the ones needed,
 * give them display names and field IDs, and optionally add fields not on the document for manual entry.
 * After saving, the document is reprocessed with the new template.
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
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [rows, setRows] = useState<Row[]>(() => initialRows(doc))
  const [title, setTitle] = useState(doc.filename.replace(/\.[^.]+$/, ''))
  const [category, setCategory] = useState(t('其他'))
  const [description, setDescription] = useState('')
  const update = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const chosen = rows.filter((r) => r.include)
  const problem =
    (!title.trim() ? t('请输入模板名称') : null) ??
    (chosen.length === 0 ? t('请至少选择一个字段') : null) ??
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
      void message.success(t('已保存为模板「{{title}}」，此文档已更新为新模板的结果', { title: template.title }))
      onClose()
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={980}
      title={t('保存为模板')}
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          disabled={problem !== null}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          {t('保存模板（{{length}} 个字段）', { length: chosen.length })}
        </Button>
      }
    >
      <Flex vertical gap={12}>
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          {t('所选的值将原样保留（包括值、出处及人工修改），不重新抽取。单据上的原名称将记为别名，便于模型识别同类单据。规则和表格可之后在“模板”页添加。')}
        </Typography.Paragraph>
        {problem && <Alert type="warning" showIcon title={problem} />}
        <Flex gap={8} wrap>
          <Input addonBefore={t('模板名称')} value={title} onChange={(e) => setTitle(e.target.value)} style={{ width: 300 }} />
          <AutoComplete
            value={category}
            options={CATEGORIES.map((c) => ({ value: c }))}
            onChange={setCategory}
            style={{ width: 140 }}
          />
          <Input
            placeholder={t('说明（选填）')}
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
              title: t('选用'),
              width: 48,
              render: (_, r, i) => <Checkbox checked={r.include} onChange={(e) => update(i, { include: e.target.checked })} />,
            },
            {
              title: t('单据上的名称 / 值'),
              width: 220,
              render: (_, r) =>
                r.source ? (
                  <Flex vertical>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {r.original}
                    </Typography.Text>
                    <Typography.Text ellipsis style={{ maxWidth: 200 }}>
                      {r.sample ?? t('（空）')}
                    </Typography.Text>
                  </Flex>
                ) : (
                  <Typography.Text type="secondary">{t('自定义字段（空值，审核时填写）')}</Typography.Text>
                ),
            },
            {
              title: t('名称'),
              width: 160,
              render: (_, r, i) => (
                <Input value={r.label} disabled={!r.include} onChange={(e) => update(i, { label: e.target.value })} />
              ),
            },
            {
              title: t('字段 ID'),
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
              title: t('类型'),
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
              title: t('必填'),
              width: 60,
              render: (_, r, i) => (
                <Switch size="small" checked={r.required} disabled={!r.include} onChange={(required) => update(i, { required })} />
              ),
            },
            {
              title: t('敏感'),
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
                    aria-label={t('删除')}
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
          {t('添加自定义字段')}
        </Button>
      </Flex>
    </Drawer>
  )
}
