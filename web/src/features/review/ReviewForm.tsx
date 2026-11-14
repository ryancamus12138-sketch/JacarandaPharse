import { CheckOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons'
import { Button, Checkbox, Flex, Form, Input } from 'antd'
import { useRef } from 'react'
import type { FieldValue } from '../../api/types'
import { shownValue } from './fields'
import type { Revise } from './useRevise'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const PLACEHOLDER: Record<FieldValue['type'], string> = {
  string: '',
  get number() { return t('数字，例如 1.00') },
  get date() { return t('日期，例如 2018-12-25') },
  get date_range() { return t('原文，或 2014-07-01 ~ 2014-07-31') },
}

interface ReviewFormValues {
  value: string
  missing: boolean
  reason: string
}

interface Props {
  value: FieldValue
  revise: Revise
  /** After a successful review (the parent uses it to jump to the next field awaiting confirmation). */
  onDone: () => void
}

/** Reason filled in automatically for "use source value": states how the source text reads. */
function adoptReason(v: FieldValue): string {
  if (v.type === 'number') {
    return Number(v.source_value) < 0 ? t('采用原文值：原文为负数（折扣或退款）') : t('采用原文值：原文为正数')
  }
  return t('采用原文值：按原文写法')
}

/**
 * Expanded for the selected field: either confirm that the value is correct, or enter a new value and reason
 * and save.
 * When a source value exists (the model's value differs from the source text), "use source value" fills in
 * the source notation and reason for review before saving (the source blocks may contain extra text that can
 * be removed first).
 */
export function ReviewForm({ value: v, revise, onDone }: Props) {
  const { t } = useTranslation()
  const [form] = Form.useForm<ReviewFormValues>()
  const missing = Form.useWatch('missing', form)
  const busy = revise.isPending && revise.variables?.valueId === v.id

  const range = useRef<[string, string]>([v.range?.start ?? '', v.range?.end ?? ''])

  /** Start or end date changed: write the value in the uniform format (only when both are present). */
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
    // Clicks inside the form must not trigger selection of the row outside.
    <div onClick={(e) => e.stopPropagation()} style={{ margin: '8px 0 0 0' }}>
      <Form<ReviewFormValues>
        form={form}
        size="small"
        layout="vertical"
        initialValues={{
          value: shownValue(v) ?? '',
          // Manually added cells start empty awaiting input; they are not "absent from the source".
          missing: v.current_value === null && v.origin !== 'manual',
          reason: '',
        }}
        onFinish={save}
        requiredMark={false}
      >
        {v.rejected_value && v.current_value === null ? (
          // The model gave a value that could not be verified in the source and was not filled: it can be
          // accepted after checking the image.
          <Flex align="center" gap={6} wrap style={{ marginBottom: 8 }}>
            <Button
              size="small"
              icon={<FileTextOutlined />}
              onClick={() =>
                form.setFieldsValue({
                  value: v.rejected_value ?? '',
                  missing: false,
                  reason: t('核对原图后采用模型输出值'),
                })
              }
            >
              {t('采用模型输出值')}
            </Button>
            <span style={{ fontSize: 12, color: '#666', overflowWrap: 'anywhere' }}>
              {t('模型输出：{{rejected_value}}', { rejected_value: v.rejected_value })}
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
              {t('采用原文值')}
            </Button>
            <span style={{ fontSize: 12, color: '#666', overflowWrap: 'anywhere' }}>
              {t('原文：{{source_value}}', { source_value: v.source_value })}
            </span>
          </Flex>
        ) : null}
        {v.type === 'date_range' && (
          // Date range: edit start and end dates directly; the value is written as 2014-07-01 ~ 2014-07-31.
          <Flex gap={6} align="center" wrap style={{ marginBottom: 8 }}>
            <span style={{ fontSize: 12 }}>{t('开始')}</span>
            <Input
              type="date"
              size="small"
              style={{ width: 150 }}
              disabled={missing}
              defaultValue={v.range?.start ?? ''}
              onChange={(e) => setRange(0, e.target.value)}
            />
            <span style={{ fontSize: 12 }}>{t('结束')}</span>
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
          label={v.type === 'date_range' ? t('正确的值（可按原文写法填写，系统将重新拆分）') : t('正确的值')}
          rules={[{ required: !missing, whitespace: true, message: t('请输入正确的值') }]}
          style={{ marginBottom: 4 }}
        >
          <Input placeholder={PLACEHOLDER[v.type]} disabled={missing} autoComplete="off" />
        </Form.Item>
        <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
          {t('也可按住 Alt（Option）并点击左侧原图上的文字直接填入')}
        </div>
        <Form.Item name="missing" valuePropName="checked" style={{ marginBottom: 8 }}>
          <Checkbox>{t('原文中没有此字段')}</Checkbox>
        </Form.Item>
        <Form.Item
          name="reason"
          label={t('原因（修改时必填）')}
          rules={[{ required: true, whitespace: true, message: t('修改时请填写原因') }]}
          style={{ marginBottom: 8 }}
        >
          <Input placeholder={t('例如：OCR 将 1.00 识别为 100')} maxLength={500} autoComplete="off" />
        </Form.Item>
        <Flex gap={8}>
          <Button
            type={v.pending_review ? 'primary' : 'default'}
            icon={<CheckOutlined />}
            onClick={confirm}
            loading={busy && revise.variables?.change.action === 'confirm'}
          >
            {t('值正确')}
          </Button>
          <Button
            htmlType="submit"
            icon={<SaveOutlined />}
            loading={busy && revise.variables?.change.action === 'edit'}
          >
            {t('保存修改')}
          </Button>
        </Flex>
      </Form>
    </div>
  )
}
