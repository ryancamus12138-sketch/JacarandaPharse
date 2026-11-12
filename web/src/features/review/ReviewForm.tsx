import { CheckOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons'
import { Button, Checkbox, Flex, Form, Input } from 'antd'
import { useRef } from 'react'
import type { FieldValue } from '../../api/types'
import type { Revise } from './useRevise'

const PLACEHOLDER: Record<FieldValue['type'], string> = {
  string: '',
  number: '数字，比如 1.00',
  date: '日期，比如 2018-12-25',
  date_range: '原文，或者 2014-07-01 ~ 2014-07-31',
}

interface ReviewFormValues {
  value: string
  missing: boolean
  reason: string
}

interface Props {
  value: FieldValue
  revise: Revise
  /** 审核成功之后（父组件用来跳到下一个待确认的字段）。 */
  onDone: () => void
}

/** "采用原文值"时自动填的原因：说清楚原文是怎么写的。 */
function adoptReason(v: FieldValue): string {
  if (v.type === 'number') {
    return Number(v.source_value) < 0 ? '采用原文值：原文是负数（折扣或退款）' : '采用原文值：原文是正数'
  }
  return '采用原文值：按原文的写法'
}

/**
 * 选中一个字段后展开：要么确认"值是对的"，要么填新值和原因保存修改。
 * 有"原文值"（模型写的和原文不完全一样）时，多一个"采用原文值"：把原文的写法和原因填好，
 * 看一眼再点保存（原文那几块可能多带了别的字，可以先删掉）。
 */
export function ReviewForm({ value: v, revise, onDone }: Props) {
  const [form] = Form.useForm<ReviewFormValues>()
  const missing = Form.useWatch('missing', form)
  const busy = revise.isPending && revise.variables?.valueId === v.id

  const range = useRef<[string, string]>([v.range?.start ?? '', v.range?.end ?? ''])

  /** 改了开始或结束日期：值写成统一格式（两个都有才写）。 */
  function setRange(side: 0 | 1, day: string) {
    range.current[side] = day
    const [start, end] = range.current
    if (start && end) form.setFieldsValue({ value: `${start} ~ ${end}` })
  }

  function confirm() {
    const reason = (form.getFieldValue('reason') as string | undefined) ?? ''
    revise.mutate({ valueId: v.id, change: { action: 'confirm', reason } }, { onSuccess: onDone })
  }

  function save(values: ReviewFormValues) {
    revise.mutate(
      {
        valueId: v.id,
        change: {
          action: 'edit',
          value: values.missing ? null : values.value.trim(),
          reason: values.reason.trim(),
        },
      },
      { onSuccess: onDone },
    )
  }

  return (
    // 点表单里面时不要再触发外面那一行的"选中"
    <div onClick={(e) => e.stopPropagation()} style={{ margin: '8px 0 0 0' }}>
      <Form<ReviewFormValues>
        form={form}
        size="small"
        layout="vertical"
        initialValues={{
          value: v.current_value ?? '',
          // 人工新加的格子本来就是空的，等着填，不算"原文里没有"
          missing: v.current_value === null && v.origin !== 'manual',
          reason: '',
        }}
        onFinish={save}
        requiredMark={false}
      >
        {v.rejected_value && v.current_value === null ? (
          // 模型给了值，但原文里核对不上，按规则没有填（ADR 0034）：核对原图后可以采用
          <Flex align="center" gap={6} wrap style={{ marginBottom: 8 }}>
            <Button
              size="small"
              icon={<FileTextOutlined />}
              onClick={() =>
                form.setFieldsValue({
                  value: v.rejected_value ?? '',
                  missing: false,
                  reason: '核对原图后采用模型给的值',
                })
              }
            >
              采用模型给的值
            </Button>
            <span style={{ fontSize: 12, color: '#666', overflowWrap: 'anywhere' }}>
              模型给的：{v.rejected_value}
            </span>
          </Flex>
        ) : null}
        {v.source_value ? (
          <Flex align="center" gap={6} wrap style={{ marginBottom: 8 }}>
            <Button
              size="small"
              icon={<FileTextOutlined />}
              onClick={() =>
                form.setFieldsValue({ value: v.source_value ?? '', missing: false, reason: adoptReason(v) })
              }
            >
              采用原文值
            </Button>
            <span style={{ fontSize: 12, color: '#666', overflowWrap: 'anywhere' }}>
              原文：{v.source_value}
            </span>
          </Flex>
        ) : null}
        {v.type === 'date_range' && (
          // 日期范围（ADR 0039）：直接改开始、结束日期，值写成 2014-07-01 ~ 2014-07-31
          <Flex gap={6} align="center" wrap style={{ marginBottom: 8 }}>
            <span style={{ fontSize: 12 }}>开始</span>
            <Input
              type="date"
              size="small"
              style={{ width: 150 }}
              disabled={missing}
              defaultValue={v.range?.start ?? ''}
              onChange={(e) => setRange(0, e.target.value)}
            />
            <span style={{ fontSize: 12 }}>结束</span>
            <Input
              type="date"
              size="small"
              style={{ width: 150 }}
              disabled={missing}
              defaultValue={v.range?.end ?? ''}
              onChange={(e) => setRange(1, e.target.value)}
            />
          </Flex>
        )}
        <Form.Item
          name="value"
          label={v.type === 'date_range' ? '正确的值（原文写法也行，系统会重新拆开）' : '正确的值'}
          rules={[{ required: !missing, whitespace: true, message: '请输入正确的值' }]}
          style={{ marginBottom: 4 }}
        >
          <Input placeholder={PLACEHOLDER[v.type]} disabled={missing} autoComplete="off" />
        </Form.Item>
        <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
          也可以按住 Alt（Option），在左边原图上点文字直接填入
        </div>
        <Form.Item name="missing" valuePropName="checked" style={{ marginBottom: 8 }}>
          <Checkbox>原文里没有这个字段</Checkbox>
        </Form.Item>
        <Form.Item
          name="reason"
          label="原因（修改时必填）"
          rules={[{ required: true, whitespace: true, message: '修改时请写明原因' }]}
          style={{ marginBottom: 8 }}
        >
          <Input placeholder="比如：OCR 把 1.00 读成了 100" maxLength={500} autoComplete="off" />
        </Form.Item>
        <Flex gap={8}>
          <Button
            type={v.pending_review ? 'primary' : 'default'}
            icon={<CheckOutlined />}
            onClick={confirm}
            loading={busy && revise.variables?.change.action === 'confirm'}
          >
            值是对的
          </Button>
          <Button
            htmlType="submit"
            icon={<SaveOutlined />}
            loading={busy && revise.variables?.change.action === 'edit'}
          >
            保存修改
          </Button>
        </Flex>
      </Form>
    </div>
  )
}
