import { FolderOpenOutlined, InboxOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Checkbox, Flex, Input, Progress, Select, Tooltip, Typography, Upload } from 'antd'
import type { RcFile } from 'antd/es/upload'
import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { uploadDocument } from '../../api/documents'
import { listTemplates } from '../../api/templates'
import { TemplatePicker } from '../templates/TemplatePicker'
import { DISCOVER, loadUploadTemplate, saveUploadTemplate } from '../templates/uploadChoice'

const ACCEPT = '.jpg,.jpeg,.png,.webp,.tif,.tiff,.pdf'
const SUFFIXES = ACCEPT.split(',')
const MAX_MB = 50 // 和后端 .env 的 MAX_UPLOAD_MB 默认值一样；真正的限制在后端
const PARALLEL = 3 // 同时传几个：太多会把后端和浏览器都堵住

/** 一个文件里有几张单据（ADR 0036）。自己写页码范围在审核页"重新处理"里。 */
const SPLIT_OPTIONS = [
  { value: 'auto', label: '自动判断（推荐）' },
  { value: 'one', label: '整个文件是一张单据' },
  { value: 'pages', label: '每页是一张单据' },
]

interface Batch {
  total: number
  done: number
  skipped: number
  failed: { name: string; reason: string }[]
}

const EMPTY: Batch = { total: 0, done: 0, skipped: 0, failed: [] }

/** 文件所在的文件夹：选整个文件夹时浏览器给出 "出差/酒店/a.pdf"，取 "出差/酒店"。 */
function folderOf(file: RcFile, extra: string): string | undefined {
  const relative = file.webkitRelativePath || ''
  const parts = relative.split('/').slice(0, -1)
  const folder = [extra.trim(), ...parts].filter(Boolean).join('/')
  return folder || undefined
}

/**
 * 上传：先选模板，再拖进文件，或者选整个文件夹（ADR 0036 批量导入）。
 * 一次几百个文件也可以：同时传 3 个，上面显示传了多少、跳过多少重复的、哪些失败了。
 * 选文件夹时记下每个文件在哪个子文件夹，列表可以按文件夹筛选。
 */
export function UploadArea() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [template, setTemplate] = useState(loadUploadTemplate)
  const [split, setSplit] = useState('auto')
  const [skipDuplicate, setSkipDuplicate] = useState(true)
  const [group, setGroup] = useState('')
  const [batch, setBatch] = useState<Batch>(EMPTY)
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  // 还没有自己的模板、也没选：抽出所有键值对（内置的收据模板不再给网页用）
  const hasOwn = (templates.data ?? []).some((t) => t.kind === 'mine' && !t.archived)
  const chosen = template ?? (templates.isSuccess && !hasOwn ? DISCOVER : undefined)
  const queue = useRef<{ file: RcFile; folder?: string }[]>([])
  const running = useRef(0)

  const pump = () => {
    while (running.current < PARALLEL && queue.current.length > 0) {
      const next = queue.current.shift()
      if (!next) break
      running.current += 1
      uploadDocument(next.file, chosen, { folder: next.folder, split, skipDuplicate })
        .then((document) => {
          setBatch((b) => ({ ...b, done: b.done + 1, skipped: b.skipped + (document.duplicate_skipped ? 1 : 0) }))
        })
        .catch((err: unknown) => {
          const reason = err instanceof ApiError ? err.message : String(err)
          setBatch((b) => ({ ...b, done: b.done + 1, failed: [...b.failed, { name: next.file.name, reason }] }))
        })
        .finally(() => {
          running.current -= 1
          void queryClient.invalidateQueries({ queryKey: ['documents'] })
          void queryClient.invalidateQueries({ queryKey: ['folders'] })
          pump()
        })
    }
  }

  const add = (file: RcFile) => {
    const wanted = SUFFIXES.some((s) => file.name.toLowerCase().endsWith(s))
    // 文件夹里的别的文件（.DS_Store、Word）不传，也不报错
    if (!wanted || file.name.startsWith('.')) return Upload.LIST_IGNORE
    if (file.size > MAX_MB * 1024 * 1024) {
      void message.error(`${file.name} 超过 ${MAX_MB} MB`)
      return Upload.LIST_IGNORE
    }
    // 上一批传完了才清零，否则接着累计
    setBatch((b) => (b.done === b.total ? { ...EMPTY, total: 1 } : { ...b, total: b.total + 1 }))
    queue.current.push({ file, folder: folderOf(file, group) })
    setTimeout(pump)
    return false // 不用 Ant Design 自己的上传，交给上面的队列
  }

  const busy = batch.total > 0 && batch.done < batch.total
  return (
    <>
      <TemplatePicker
        value={template}
        onChange={(value) => {
          setTemplate(value)
          saveUploadTemplate(value)
        }}
      />
      <Flex gap={12} wrap align="center" style={{ marginBottom: 12 }}>
        <Flex align="center" gap={8}>
          <Tooltip title="一个 PDF 里扫了好几张发票，或者一张账单有好几页。自动判断：看页上的“第 2 页 共 3 页”，没有就让模型判断这一页是不是上一页的续页">
            <Typography.Text>一个文件里有几张单据：</Typography.Text>
          </Tooltip>
          <Select value={split} onChange={setSplit} options={SPLIT_OPTIONS} style={{ width: 200 }} />
        </Flex>
        <Checkbox checked={skipDuplicate} onChange={(e) => setSkipDuplicate(e.target.checked)}>
          跳过以前传过的文件（内容一样的）
        </Checkbox>
        <Input
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          placeholder="归到文件夹（可以不填），如 2026-10 出差"
          allowClear
          style={{ width: 280 }}
        />
      </Flex>
      <Upload.Dragger
        accept={ACCEPT}
        multiple
        showUploadList={false}
        beforeUpload={add}
        style={{ marginBottom: 8 }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">把收据、发票拖到这里，或者点击选择文件（一次可以选几百个）</p>
        <p className="ant-upload-hint">
          支持图片（JPG、PNG、WebP、TIFF）和 PDF，单个文件最大 {MAX_MB} MB
        </p>
      </Upload.Dragger>
      <Flex gap={8} align="center" wrap style={{ marginBottom: 16 }}>
        <Upload directory multiple accept={ACCEPT} showUploadList={false} beforeUpload={add}>
          <Button icon={<FolderOpenOutlined />}>选择整个文件夹</Button>
        </Upload>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          子文件夹也会一起导入，列表里可以按文件夹筛选。要自动导入一个文件夹，见“系统信息”里的监视文件夹。
        </Typography.Text>
      </Flex>
      {batch.total > 0 && (
        <Alert
          type={batch.failed.length > 0 ? 'warning' : busy ? 'info' : 'success'}
          showIcon
          closable={!busy}
          onClose={() => setBatch(EMPTY)}
          style={{ marginBottom: 16 }}
          title={
            busy
              ? `正在上传 ${batch.done} / ${batch.total}`
              : `传完了 ${batch.total} 个文件，正在排队处理`
          }
          description={
            <>
              <Progress
                percent={Math.round((batch.done / batch.total) * 100)}
                size="small"
                status={busy ? 'active' : batch.failed.length ? 'exception' : 'success'}
              />
              {batch.skipped > 0 && <div>跳过 {batch.skipped} 个以前传过的文件</div>}
              {batch.failed.length > 0 && (
                <div>
                  {batch.failed.length} 个没传上：
                  {batch.failed.slice(0, 5).map((f) => (
                    <div key={f.name}>
                      {f.name}：{f.reason}
                    </div>
                  ))}
                  {batch.failed.length > 5 && <div>……</div>}
                </div>
              )}
            </>
          }
        />
      )}
    </>
  )
}
