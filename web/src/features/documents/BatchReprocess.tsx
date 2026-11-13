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
 * 批量重新处理：勾选的几份用同样的设置各跑新的一轮。设置都显示出来，可以改；
 * 默认模板用各自的最新版、分单据和上次一样、看原图和 PDF 读法按系统设置。
 */
export function BatchReprocessButton({
  ids,
  busy,
  onBusy,
  onDone,
}: {
  ids: string[]
  /** 选中的里面有正在处理的：不打开设置，先提示。 */
  busy: boolean
  onBusy: () => void
  onDone: (ids: string[]) => void
}) {
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
      if (queued > 0) void message.success(`已重新排队 ${queued} 份`)
      for (const r of skipped) void message.warning(`没重新处理：${r}`)
      onDone(ids)
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const keep = { value: KEEP, label: '和上次一样' }
  return (
    <>
      <Tooltip title={ids.length === 0 ? '先在左边勾选要重新处理的' : undefined}>
        <Button
          icon={<SyncOutlined />}
          disabled={ids.length === 0}
          loading={run.isPending}
          onClick={() => (busy ? onBusy() : setOpen(true))}
        >
          重新处理{ids.length > 0 ? `（${ids.length}）` : ''}
        </Button>
      </Tooltip>
      <Modal
        open={open}
        title={`重新处理选中的 ${ids.length} 份`}
        okText="重新处理"
        cancelText="取消"
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
          <Form.Item name="template" label="用哪个模板">
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: '不换',
                  title: 'same',
                  options: [{ value: SAME, label: '各自现在的模板（最新版）' }],
                },
                ...templateOptions(templates.data ?? []),
              ]}
            />
          </Form.Item>
          <Form.Item name="split" label="一个文件里几张单据">
            <Select options={[keep, ...SPLITS.filter((s) => s.value !== 'ranges')]} />
          </Form.Item>
          <Form.Item
            name="vision"
            label="对照原图读"
            extra={vision.isSuccess && !sees ? '还没有视觉模型，只能看文字。' : undefined}
          >
            <Select disabled={!sees} options={[...VISION_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="pdfRead" label="PDF 怎么读" extra="只对 PDF 有效。">
            <Select options={[...PDF_READ_CHOICES, keep]} />
          </Form.Item>
          <Form.Item name="reason" label="为什么" rules={[{ required: true }]}>
            <Radio.Group
              options={Object.entries(REPROCESS_REASONS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item
            name="note"
            label="说明"
            rules={[{ required: reason === 'other', message: '原因选了"其他"，请写一句说明' }]}
          >
            <Input.TextArea autoSize={{ minRows: 1 }} maxLength={500} placeholder="可以不写" />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>旧结果保留，已审核的要重新审核。</div>
        </Form>
      </Modal>
    </>
  )
}
