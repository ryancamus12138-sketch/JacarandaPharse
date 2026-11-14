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
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const ACCEPT = '.jpg,.jpeg,.png,.webp,.tif,.tiff,.pdf'
const SUFFIXES = ACCEPT.split(',')
const MAX_MB = 50 // Same as the backend default of MAX_UPLOAD_MB in .env; the backend enforces the actual limit.
const PARALLEL = 3 // Concurrent uploads: more would congest both backend and browser.

/** Number of documents per file. Custom page ranges are set under "Reprocess" on the review page. */
const SPLIT_OPTIONS = [
  { value: 'auto', get label() { return t('自动判断（推荐）') } },
  { value: 'one', get label() { return t('整个文件是一张单据') } },
  { value: 'pages', get label() { return t('每页是一张单据') } },
]

interface Batch {
  total: number
  done: number
  skipped: number
  failed: { name: string; reason: string }[]
}

const EMPTY: Batch = { total: 0, done: 0, skipped: 0, failed: [] }

/**
 * Source folder of the file: when a whole folder is selected the browser reports "trip/hotel/a.pdf"; take
 * "trip/hotel".
 */
function folderOf(file: RcFile, extra: string): string | undefined {
  const relative = file.webkitRelativePath || ''
  const parts = relative.split('/').slice(0, -1)
  const folder = [extra.trim(), ...parts].filter(Boolean).join('/')
  return folder || undefined
}

/**
 * Upload: choose a template, then drop files or select a whole folder (batch import). Hundreds of files at
 * once are fine: 3 upload concurrently, with counts of uploaded, skipped duplicates and failures shown above.
 * When a folder is selected, each file's subfolder is recorded so the list can filter by folder.
 */
export function UploadArea() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [template, setTemplate] = useState(loadUploadTemplate)
  const [split, setSplit] = useState('auto')
  const [skipDuplicate, setSkipDuplicate] = useState(true)
  const [group, setGroup] = useState('')
  const [batch, setBatch] = useState<Batch>(EMPTY)
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  // No templates of one's own and none selected: extract all key-value pairs (the built-in receipt template
  // is no longer offered in the web UI).
  const hasOwn = (templates.data ?? []).some((tpl) => tpl.kind === 'mine' && !tpl.archived)
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
    // Other files in the folder (.DS_Store, Word documents) are skipped without errors.
    if (!wanted || file.name.startsWith('.')) return Upload.LIST_IGNORE
    if (file.size > MAX_MB * 1024 * 1024) {
      void message.error(t('{{name}} 超过 {{MAX_MB}} MB', { name: file.name, MAX_MB }))
      return Upload.LIST_IGNORE
    }
    // Reset only after the previous batch finished; otherwise keep accumulating.
    setBatch((b) => (b.done === b.total ? { ...EMPTY, total: 1 } : { ...b, total: b.total + 1 }))
    queue.current.push({ file, folder: folderOf(file, group) })
    setTimeout(pump)
    return false // Ant Design's own upload is not used; the queue above handles it.
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
          <Tooltip title={t('适用于一个 PDF 中扫描了多张发票，或一张账单包含多页的情况。自动判断：依据页面上的“第 2 页 共 3 页”等页码信息；无页码时由模型判断该页是否为上一页的续页')}>
            <Typography.Text>{t('每个文件的单据数：')}</Typography.Text>
          </Tooltip>
          <Select value={split} onChange={setSplit} options={SPLIT_OPTIONS} style={{ width: 240 }} />
        </Flex>
        <Checkbox checked={skipDuplicate} onChange={(e) => setSkipDuplicate(e.target.checked)}>
          {t('跳过已上传过的文件（内容相同）')}
        </Checkbox>
        <Input
          value={group}
          onChange={(e) => setGroup(e.target.value)}
          placeholder={t('归入文件夹（选填），如“2026-10 出差”')}
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
        <p className="ant-upload-text">{t('将收据、发票拖到此处，或点击选择文件（单次可选择数百个）')}</p>
        <p className="ant-upload-hint">
          {t('支持图片（JPG、PNG、WebP、TIFF）和 PDF，单个文件最大 {{MAX_MB}} MB', { MAX_MB })}
        </p>
      </Upload.Dragger>
      <Flex gap={8} align="center" wrap style={{ marginBottom: 16 }}>
        <Upload directory multiple accept={ACCEPT} showUploadList={false} beforeUpload={add}>
          <Button icon={<FolderOpenOutlined />}>{t('选择整个文件夹')}</Button>
        </Upload>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('子文件夹将一并导入，可在列表中按文件夹筛选。如需自动导入某个文件夹，请参阅“系统信息”中的监视文件夹。')}
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
              ? t('正在上传 {{done}} / {{total}}', { done: batch.done, total: batch.total })
              : t('已上传 {{total}} 个文件，正在排队处理', { total: batch.total })
          }
          description={
            <>
              <Progress
                percent={Math.round((batch.done / batch.total) * 100)}
                size="small"
                status={busy ? 'active' : batch.failed.length ? 'exception' : 'success'}
              />
              {batch.skipped > 0 && <div>{t('已跳过 {{skipped}} 个已上传过的文件', { skipped: batch.skipped })}</div>}
              {batch.failed.length > 0 && (
                <div>
                  {t('{{length}} 个文件上传失败：', { length: batch.failed.length })}
                  {batch.failed.slice(0, 5).map((f) => (
                    <div key={f.name}>
                      {t('{{name}}：{{reason}}', { name: f.name, reason: f.reason })}
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
