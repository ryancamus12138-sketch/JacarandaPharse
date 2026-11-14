import { SyncOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { App, Button, Form, Input, Modal, Radio, Select, Tooltip } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { type BatchReprocess, reprocessDocuments } from '../../api/documents'
import { getVision } from '../../api/system'
import { listTemplates } from '../../api/templates'
import { REPROCESS_REASONS, type ReprocessReason } from '../../api/types'
import { PDF_READ_CHOICES, SAME, SPLITS, VISION_CHOICES } from '../review/reprocessChoices'
import { templateOptions } from '../templates/uploadChoice'
import { useTranslation } from 'react-i18next'

const KEEP = 'keep'

interface Choice {
  template: string
  split: string
  vision: string
  pdfRead: string
  reason: ReprocessReason
  note: string
}

/**
 * Bulk reprocessing: run a new pass for each selected document with the same settings. All settings are shown
 * and editable; by default each document uses its latest template version, the previous split, and system
 * settings for vision and PDF read mode.
 */
export function BatchReprocessButton({
  ids,
  busy,
  onBusy,
  onDone,
}: {
  ids: string[]
  /** The selection contains documents being processed: show a notice instead of the settings. */
  busy: boolean
  onBusy: () => void
  onDone: (ids: string[]) => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<Choice>()
  const reason = Form.useWatch('reason', form)
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, enabled: open })
  const vision = useQuery({ queryKey: ['vision'], queryFn: getVision, enabled: open, staleTime: 60_000 })
  const sees = vision.data?.sees_images ?? false

  const run = useMutation({
    mutationFn: (v: Choice) => {
      const settings: BatchReprocess = {
        template: v.template === SAME ? undefined : v.template,
        split: v.split === KEEP ? null : (v.split as BatchReprocess['split']),
        vision: v.vision === KEEP ? null : (v.vision as BatchReprocess['vision']),
        pdf_read: v.pdfRead === KEEP ? null : (v.pdfRead as BatchReprocess['pdf_read']),
        reason: v.reason,
        note: v.note ?? '',
      }
      return reprocessDocuments(ids, settings)
    },
    onSuccess: ({ queued, skipped }) => {
      setOpen(false)
      if (queued > 0) void message.success(t('已重新排队 {{queued}} 份', { queued }))
      for (const r of skipped) void message.warning(t('没重新处理：{{r}}', { r }))
      onDone(ids)
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const keep = { value: KEEP, label: t('和上次一样') }
  return (
    <>
      <Tooltip title={ids.length === 0 ? t('先在左边勾选要重新处理的') : undefined}>
        <Button
          icon={<SyncOutlined />}
          disabled={ids.length === 0}
          loading={run.isPending}
          onClick={() => (busy ? onBusy() : setOpen(true))}
        >
          {t('重新处理')}{ids.length > 0 ? t('（{{length}}）', { length: ids.length }) : ''}
        </Button>
      </Tooltip>
      <Modal
        open={open}
        title={t('重新处理选中的 {{length}} 份', { length: ids.length })}
        okText={t('重新处理')}
        cancelText={t('取消')}
        confirmLoading={run.isPending}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            template: SAME,
            split: KEEP,
            vision: 'default',
            pdfRead: 'default',
            reason: 'batch',
            note: '',
          }}
          onFinish={(v) => run.mutate(v)}
          preserve={false}
        >
          <Form.Item name="template" label={t('用哪个模板')}>
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: t('不换'),
                  title: 'same',
                  options: [{ value: SAME, label: t('各自现在的模板（最新版）') }],
                },
                ...templateOptions(templates.data ?? []),
              ]}
            />
          </Form.Item>
          <Form.Item name="split" label={t('一个文件里几张单据')}>
            <Select options={[keep, ...SPLITS.filter((s) => s.value !== 'ranges')]} />
          </Form.Item>
          <Form.Item
            name="vision"
            label={t('对照原图读')}
            extra={vision.isSuccess && !sees ? t('还没有视觉模型，只能看文字。') : undefined}
          >
            <Select disabled={!sees} options={[...VISION_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="pdfRead" label={t('PDF 怎么读')} extra={t('只对 PDF 有效。')}>
            <Select options={[...PDF_READ_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="reason" label={t('为什么')} rules={[{ required: true }]}>
            <Radio.Group
              options={Object.entries(REPROCESS_REASONS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item
            name="note"
            label={t('说明')}
            rules={[{ required: reason === 'other', message: t('原因选了"其他"，请写一句说明') }]}
          >
            <Input.TextArea autoSize={{ minRows: 1 }} maxLength={500} placeholder={t('可以不写')} />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>{t('旧结果保留，已审核的要重新审核。')}</div>
        </Form>
      </Modal>
    </>
  )
}
