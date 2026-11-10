import { CheckOutlined, SaveOutlined } from '@ant-design/icons'
import { Button, Checkbox, Flex, Form, Input } from 'antd'
import type { FieldValue } from '../../api/types'
import type { Revise } from './useRevise'

const PLACEHOLDER: Record<FieldValue['type'], string> = {
  string: '',
  number: '数字，比如 1.00',
  date: '日期，比如 2018-12-25',
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

/** 选中一个字段后展开：要么确认"值是对的"，要么填新值和原因保存修改。 */
export function ReviewForm({ value: v, revise, onDone }: Props) {
  const [form] = Form.useForm<ReviewFormValues>()
  const missing = Form.useWatch('missing', form)
  const busy = revise.isPending && revise.variables?.valueId === v.id

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
    <div onClick={(e) => e.stopPropagation()} style={{ margin: '8px 0 0 88px' }}>
      <Form<ReviewFormValues>
        form={form}
        size="small"
        layout="vertical"
        initialValues={{
          value: v.current_value ?? '',
          missing: v.current_value === null,
          reason: '',
        }}
        onFinish={save}
        requiredMark={false}
      >
        <Form.Item
          name="value"
          label="正确的值"
          rules={[{ required: !missing, whitespace: true, message: '请输入正确的值' }]}
          style={{ marginBottom: 4 }}
        >
          <Input placeholder={PLACEHOLDER[v.type]} disabled={missing} autoComplete="off" />
        </Form.Item>
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
