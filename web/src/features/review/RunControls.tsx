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
import { useDesktop } from '../../desktop'
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
          t('第 {{run}} 轮', { run: r.run }) + (r.current ? t('（当前）') : ''),
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
  const desktop = useDesktop()
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
      void message.success(t('已加入队列重新处理。处理完成后将显示新结果，旧结果仍可在轮次中查看'))
      void queryClient.invalidateQueries({ queryKey: ['document', doc.id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <>
      <Tooltip title={busy ? t('正在处理，请等待处理完成') : t('更换模板或使用模板的新版本重新抽取')}>
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
          <Form.Item name="template" label={t('使用模板')}>
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: t('不更换'),
                  title: 'same',
                  options: [
                    {
                      value: SAME,
                      label: doc.auto ? t('重新自动选择') : t('当前模板的最新版本'),
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
              label={t('此文件中的单据数')}
              extra={t('如拆分有误（如两张发票被识别为一张，或一张两页的账单被拆为两张），可在此修改。')}
            >
              <Select options={SPLITS} />
            </Form.Item>
          )}
          {doc.page_count > 1 && split === 'ranges' && (
            <Form.Item
              name="ranges"
              label={t('页码（共 {{page_count}} 页）', { page_count: doc.page_count })}
              extra={t('每张单据所占页码，以逗号分隔，例如 1-2, 3, 4-6。未列出的页按自动判断追加。')}
              rules={[{ required: true, message: t('请填写每张单据所占的页码') }]}
            >
              <Input placeholder="1-2, 3, 4-6" />
            </Form.Item>
          )}
          <Form.Item
            name="vision"
            label={t('对照原图读取')}
            extra={
              sees
                ? t('模型默认仅读取 OCR 识别的文字。对照原图（整页图像）读取时，手写、印章、模糊或被笔迹遮挡的文字更准确，但速度较慢。“系统决定”：仅在照片不清晰时对照原图。')
                : vision.isSuccess
                  ? desktop
                    ? t('尚未配置视觉模型，模型仅能读取文字。在「设置 → 模型」中配置视觉模型并重新启动 JacarandaParse 后即可选择。')
                    : t('尚未配置视觉模型，模型仅能读取文字。将 mmproj-F16.gguf 放入 models/ 并重新执行 jacara start 后即可选择（详见系统信息）。')
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
              label={t('PDF 读取方式')}
              extra={t('渲染为图像识别：与照片流程相同，PDF 文本层仅用于核对（部分 PDF 文本层存在错误，如将“示”存为部首“⽰”、将 0 存为 8）。仅读取文本层：速度快，仅适用于确认无误的电子版。')}
            >
              <Radio.Group optionType="button" options={PDF_READ_CHOICES} />
            </Form.Item>
          )}
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
            <Input.TextArea autoSize={{ minRows: 2 }} maxLength={500} placeholder={t('将记录在处理历史和审计日志中')} />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>
            {t('原有结果和审核记录均会保留；新一轮完成后，上一轮经人工审核、且本轮值与位置均一致的字段，可通过“沿用上一轮审核”一次性沿用。')}
            {doc.status === 'reviewed' && t(' 此文档已完成审核，重新处理后需要重新审核。')}
          </div>
        </Form>
      </Modal>
    </>
  )
}
