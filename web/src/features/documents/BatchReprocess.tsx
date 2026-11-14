import { SyncOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, Modal, Radio, Select, Tooltip } from 'antd'
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
  running,
  onDone,
}: {
  ids: string[]
  /** How many of them are queued or being processed: their current processing stops and starts over. */
  running: number
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
      if (queued > 0) void message.success(t('已重新加入队列 {{queued}} 份', { queued }))
      for (const r of skipped) void message.warning(t('未重新处理：{{r}}', { r }))
      onDone(ids)
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const keep = { value: KEEP, label: t('与上次相同') }
  return (
    <>
      <Tooltip title={ids.length === 0 ? t('请先在左侧勾选要重新处理的文档') : undefined}>
        <Button
          icon={<SyncOutlined />}
          disabled={ids.length === 0}
          loading={run.isPending}
          onClick={() => setOpen(true)}
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
        {running > 0 && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            title={t('其中 {{running}} 份正在排队或处理：将停止当前处理，按下面的设置从头开始。', { running })}
          />
        )}
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
          <Form.Item name="template" label={t('使用模板')}>
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: t('不更换'),
                  title: 'same',
                  options: [{ value: SAME, label: t('各自当前的模板（最新版本）') }],
                },
                ...templateOptions(templates.data ?? []),
              ]}
            />
          </Form.Item>
          <Form.Item name="split" label={t('每个文件的单据数')}>
            <Select options={[keep, ...SPLITS.filter((s) => s.value !== 'ranges')]} />
          </Form.Item>
          <Form.Item
            name="vision"
            label={t('对照原图读取')}
            extra={vision.isSuccess && !sees ? t('尚未配置视觉模型，仅能读取文字。') : undefined}
          >
            <Select disabled={!sees} options={[...VISION_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="pdfRead" label={t('PDF 读取方式')} extra={t('只对 PDF 有效。')}>
            <Select options={[...PDF_READ_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="reason" label={t('原因')} rules={[{ required: true }]}>
            <Radio.Group
              options={Object.entries(REPROCESS_REASONS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item
            name="note"
            label={t('说明')}
            rules={[{ required: reason === 'other', message: t('原因为“其他”时，请填写说明') }]}
          >
            <Input.TextArea autoSize={{ minRows: 1 }} maxLength={500} placeholder={t('选填')} />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>{t('原有结果将保留，已审核的文档需要重新审核。')}</div>
        </Form>
      </Modal>
    </>
  )
}
