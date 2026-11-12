import { HistoryOutlined, RedoOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Form, Input, Modal, Radio, Select, Tooltip } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { reprocessDocument } from '../../api/documents'
import { listTemplates } from '../../api/templates'
import {
  type DocumentDetail,
  REPROCESS_REASONS,
  type ReprocessReason,
} from '../../api/types'
import { formatTime } from '../../utils/format'
import { templateOptions } from '../templates/uploadChoice'

/** 看第几轮的结果。只有一轮时不显示。 */
export function RunSwitcher({
  doc,
  onChange,
}: {
  doc: DocumentDetail
  onChange: (run: number | undefined) => void
}) {
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
        label: `第 ${r.run} 轮${r.current ? '（现在用的）' : ''} · ${r.template_title} · ${formatTime(r.created_at)}${r.reason ? ` · ${r.reason}` : ''}`,
      }))}
      style={{ maxWidth: 360 }}
    />
  )
}

const SAME = '__same__'

/** 重新处理：换模板（或同一个模板的最新版）跑新的一轮，要写原因。旧结果留着可以查看。 */
export function ReprocessButton({ doc }: { doc: DocumentDetail }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<{ template: string; reason: ReprocessReason; note: string }>()
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates, enabled: open })
  const reason = Form.useWatch('reason', form)
  const busy = doc.status === 'uploaded' || doc.status === 'processing'

  const reprocess = useMutation({
    mutationFn: (v: { template: string; reason: ReprocessReason; note: string }) =>
      reprocessDocument(doc.id, {
        template: v.template === SAME ? undefined : v.template,
        reason: v.reason,
        note: v.note ?? '',
      }),
    onSuccess: () => {
      setOpen(false)
      void message.success('已放进队列重新处理。处理完会换成新结果，旧结果还能在"第几轮"里查看')
      void queryClient.invalidateQueries({ queryKey: ['document', doc.id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  return (
    <>
      <Tooltip title={busy ? '正在处理，等处理完再说' : '换模板或用模板的新版本再抽一次'}>
        <Button icon={<RedoOutlined />} disabled={busy} onClick={() => setOpen(true)}>
          重新处理
        </Button>
      </Tooltip>
      <Modal
        open={open}
        title="重新处理"
        okText="重新处理"
        cancelText="取消"
        confirmLoading={reprocess.isPending}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ template: SAME, reason: 'template_changed', note: '' }}
          onFinish={(v) => reprocess.mutate(v)}
          preserve={false}
        >
          <Form.Item name="template" label="用哪个模板">
            <Select
              loading={templates.isPending}
              options={[
                {
                  label: '不换',
                  title: 'same',
                  options: [{ value: SAME, label: '现在这个模板的最新版' }],
                },
                ...templateOptions(templates.data ?? []),
              ]}
            />
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
            <Input.TextArea autoSize={{ minRows: 2 }} maxLength={500} placeholder="会记在处理历史和审计日志里" />
          </Form.Item>
          <div style={{ color: 'rgba(0,0,0,0.45)', fontSize: 12 }}>
            旧的结果和审核记录都会留着；新一轮出来后，上次人审核过、这次值和位置都一样的字段，
            可以用"采纳上次的确认"一次沿用。
            {doc.status === 'reviewed' && ' 这份已经审核完了，重新处理后要重新审核。'}
          </div>
        </Form>
      </Modal>
    </>
  )
}
