import { HistoryOutlined, RedoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Form, Input, Modal, Radio, Select, Tooltip } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { reprocessDocument } from '../../api/documents'
import { getVision } from '../../api/system'
import { listTemplates } from '../../api/templates'
import {
  type DocumentDetail,
  REPROCESS_REASONS,
  type ReprocessReason,
} from '../../api/types'
import { formatTime } from '../../utils/format'
import { templateOptions } from '../templates/uploadChoice'
import {
  PDF_READ_CHOICES,
  type PdfRead,
  SAME,
  SPLITS,
  VISION_CHOICES,
  type Vision,
} from './reprocessChoices'
import { useTranslation } from 'react-i18next'

/** Run to show. Hidden when there is only one run. */
export function RunSwitcher({
  doc,
  onChange,
}: {
  doc: DocumentDetail
  onChange: (run: number | undefined) => void
}) {
  const { t } = useTranslation()
  const runs = doc.runs ?? []
  if (runs.length <= 1) return null
  return (
    <Select
      value={doc.viewing_run}
      onChange={(run) => onChange(runs.find((r) => r.run === run)?.current ? undefined : run)}
      popupMatchSelectWidth={false}
      prefix={<HistoryOutlined />}
      options={runs.map((r) => ({
        value: r.run,
        label: [
          t('第 {{run}} 轮', { run: r.run }) + (r.current ? t('（现在用的）') : ''),
          r.template_title,
          formatTime(r.created_at),
          ...(r.reason ? [r.reason] : []),
        ].join(' · '),
      }))}
      style={{ maxWidth: 360 }}
    />
  )
}


/**
 * Reprocess with another template (or the latest version of the same one) as a new run; a reason is required.
 * Earlier results remain viewable.
 */
interface Choice {
  template: string
  vision: Vision
  pdfRead: PdfRead
  reason: ReprocessReason
  note: string
  split: string
  ranges?: string
}



/** Current split mode → form option. */
function splitChoice(split: string | undefined): { split: string; ranges?: string } {
  if (!split || ['auto', 'one', 'pages'].includes(split)) return { split: split || 'auto' }
  return { split: 'ranges', ranges: split }
}

export function ReprocessButton({ doc }: { doc: DocumentDetail }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<Choice>()
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, enabled: open })
  const reason = Form.useWatch('reason', form)
  const split = Form.useWatch('split', form)
  const busy = doc.status === 'uploaded' || doc.status === 'processing'
  const vision = useQuery({ queryKey: ['vision'], queryFn: getVision, enabled: open, staleTime: 60_000 })
  const sees = vision.data?.sees_images ?? false
  const currentVision: Vision = doc.vision === 'always' || doc.vision === 'off' ? doc.vision : 'default'
  const currentPdfRead: PdfRead = doc.pdf_read === 'image' || doc.pdf_read === 'text' ? doc.pdf_read : 'default'
  const isPdf = doc.content_type === 'application/pdf'

  const reprocess = useMutation({
    mutationFn: (v: Choice) =>
      reprocessDocument(doc.id, {
        template: v.template === SAME ? undefined : v.template,
        reason: v.reason,
        note: v.note ?? '',
        split: v.split === 'ranges' ? (v.ranges ?? '') : v.split,
        vision: v.vision === currentVision ? undefined : v.vision,
        pdf_read: !isPdf || v.pdfRead === currentPdfRead ? undefined : v.pdfRead,
      }),
    onSuccess: () => {
      setOpen(false)
      void message.success(t('已放进队列重新处理。处理完会换成新结果，旧结果还能在"第几轮"里查看'))
      void queryClient.invalidateQueries({ queryKey: ['document', doc.id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <>
      <Tooltip title={busy ? t('正在处理，等处理完再说') : t('换模板或用模板的新版本再抽一次')}>
        <Button icon={<RedoOutlined />} disabled={busy} onClick={() => setOpen(true)}>
          {t('重新处理')}
        </Button>
      </Tooltip>
      <Modal
        open={open}
        title={t('重新处理')}
        okText={t('重新处理')}
        cancelText={t('取消')}
        confirmLoading={reprocess.isPending}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            template: SAME,
            vision: currentVision,
            pdfRead: currentPdfRead,
            reason: 'template_changed',
            note: '',
            ...splitChoice(doc.split),
          }}
          onFinish={(v) => reprocess.mutate(v)}
          preserve={false}
        >
          <Form.Item name="template" label={t('用哪个模板')}>
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: t('不换'),
                  title: 'same',
                  options: [
                    {
                      value: SAME,
                      label: doc.auto ? t('再自动判断一次') : t('现在这个模板的最新版'),
                    },
                  ],
                },
                ...templateOptions(templates.data ?? []),
              ]}
            />
          </Form.Item>
          {doc.page_count > 1 && (
            <Form.Item
              name="split"
              label={t('这个文件里有几张单据')}
              extra={t('分错了（两张发票当成一张，或者一张两页的账单拆成两张）就在这里改。')}
            >
              <Select options={SPLITS} />
            </Form.Item>
          )}
          {doc.page_count > 1 && split === 'ranges' && (
            <Form.Item
              name="ranges"
              label={t('页码（共 {{page_count}} 页）', { page_count: doc.page_count })}
              extra={t('每张单据占哪几页，用逗号分开，例如 1-2, 3, 4-6。没写到的页按自动判断接在后面。')}
              rules={[{ required: true, message: t('写一下每张单据占哪几页') }]}
            >
              <Input placeholder="1-2, 3, 4-6" />
            </Form.Item>
          )}
          <Form.Item
            name="vision"
            label={t('对照原图读')}
            extra={
              sees
                ? t('模型平时只看 OCR 认出的文字。对照原图（整页的照片）读，手写、印章、模糊、被笔迹盖住的字更准，但慢一些。“系统决定”：照片不清楚时才对照。')
                : vision.isSuccess
                  ? t('还没有视觉模型，模型只能看文字。把 mmproj-F16.gguf 放进 models/，重新 jacara start 后可以选（见系统信息）。')
                  : undefined
            }
          >
            <Radio.Group
              optionType="button"
              disabled={!sees}
              options={VISION_CHOICES}
            />
          </Form.Item>
          {isPdf && (
            <Form.Item
              name="pdfRead"
              label={t('PDF 怎么读')}
              extra={t('画成图识别：和照片走同一条路，PDF 自带的文字只拿来核对（有的 PDF 自带的文字是错的，比如把“示”存成部首“⽰”、把 0 存成 8）。只读自带的文字：快，只适合确定干净的电子版。')}
            >
              <Radio.Group optionType="button" options={PDF_READ_CHOICES} />
            </Form.Item>
          )}
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
            <Input.TextArea autoSize={{ minRows: 2 }} maxLength={500} placeholder={t('会记在处理历史和审计日志里')} />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>
            {t('旧的结果和审核记录都会留着；新一轮出来后，上次人审核过、这次值和位置都一样的字段， 可以用"采纳上次的确认"一次沿用。')}
            {doc.status === 'reviewed' && t(' 这份已经审核完了，重新处理后要重新审核。')}
          </div>
        </Form>
      </Modal>
    </>
  )
}
