import {
  CheckCircleFilled,
  CloudDownloadOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Collapse,
  Flex,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Result,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { restartServices } from '../../api/desktop'
import { getModels } from '../../api/models'
import {
  cancelDownloads,
  chooseModelFile,
  deleteInboxFiles,
  getSetup,
  type InboxFile,
  type ItemState,
  importInboxFile,
  importModelFile,
  type ListedModel,
  type ModelKind,
  openInbox,
  pauseDownloads,
  resumeDownloads,
  type SetupItem,
  type SetupState,
  startDownloads,
} from '../../api/setup'
import { formatBytes } from '../../utils/format'
import { useMaintenanceJob } from '../settings/useMaintenanceJob'
import { t } from '../../i18n'
import { postponeSetup } from './setupGate'

type Way = 'download' | 'inbox' | 'file'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

/** 3.4 MB/s */
function formatSpeed(bytesPerSecond: number): string {
  return t('{{speed}}/秒', { speed: formatBytes(bytesPerSecond) })
}

/** 1 小时 5 分 / 3 分 20 秒 / 12 秒 */
function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return t('{{h}} 小时 {{m}} 分', { h, m })
  if (m > 0) return t('{{m}} 分 {{s}} 秒', { m, s })
  return t('{{s}} 秒', { s })
}

const STATE: Record<ItemState, { text: () => string; color: string }> = {
  waiting: { text: () => t('等待中'), color: 'default' },
  downloading: { text: () => t('下载中'), color: 'processing' },
  verifying: { text: () => t('校验中'), color: 'processing' },
  importing: { text: () => t('导入中'), color: 'processing' },
  done: { text: () => t('已就绪'), color: 'success' },
  paused: { text: () => t('已暂停'), color: 'warning' },
  failed: { text: () => t('失败'), color: 'error' },
}

function slotName(slot: string): string {
  return slot === 'mmproj' ? t('视觉模型') : t('大模型')
}

/** What one file is doing: progress, where it comes from, why a source was given up. */
function ItemRow({ item, setup }: { item: SetupItem; setup: SetupState }) {
  const sources = setup.sources
  const { t } = useTranslation()
  const label = (id: string | null) => sources.find((s) => s.id === id)?.label ?? id ?? ''
  const percent = item.total > 0 ? Math.floor((item.done / item.total) * 100) : 0
  let detail: string
  if (item.state === 'downloading') {
    detail =
      item.origin !== 'download'
        ? t('正在复制文件')
        : item.switched.length > 0
          ? t('正在从 {{source}} 下载（{{failed}} 无法使用，已自动切换）', {
              source: label(item.source),
              failed: item.switched.map((s) => s.label).join(t('、')),
            })
          : t('正在从 {{source}} 下载', { source: label(item.source) })
  } else if (item.state === 'verifying') {
    detail = t('正在核对文件大小和 SHA-256')
  } else if (item.state === 'importing') {
    detail = t('正在导入并设为使用中')
  } else if (item.state === 'done') {
    detail = item.imported ? t('已导入为 {{version}}，并设为使用中', { version: item.imported }) : ''
  } else if (item.state === 'paused') {
    detail = t('已下载 {{done}}，可以继续', { done: formatBytes(item.done) })
  } else if (item.state === 'waiting') {
    detail = t('排在前一个文件之后')
  } else {
    detail = ''
  }
  return (
    <Flex vertical gap={4} style={{ padding: '8px 0' }}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Space size={8} wrap>
          <Typography.Text strong>{setup.kinds[item.slot] ?? slotName(item.slot)}</Typography.Text>
          <Typography.Text type="secondary">{item.rel ?? item.filename}</Typography.Text>
          {item.origin === 'inbox' && <Tag>{t('放入文件夹')}</Tag>}
          {item.origin === 'file' && <Tag>{t('选择电脑上的文件')}</Tag>}
          {item.custom && <Tag>{t('自定义模型')}</Tag>}
        </Space>
        <Tag color={STATE[item.state].color}>{STATE[item.state].text()}</Tag>
      </Flex>
      {item.state !== 'done' && item.state !== 'failed' && (
        <Progress
          percent={percent}
          status={item.state === 'paused' ? 'normal' : 'active'}
          format={() => `${formatBytes(item.done)} / ${formatBytes(item.total)}`}
        />
      )}
      <Flex justify="space-between" gap={8} wrap>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {detail}
        </Typography.Text>
        {item.state === 'downloading' && item.speed > 0 && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {formatSpeed(item.speed)}
            {item.eta !== null ? t('，还需约 {{eta}}', { eta: formatDuration(item.eta) }) : ''}
          </Typography.Text>
        )}
      </Flex>
      {item.state === 'failed' && (
        <Alert
          type="error"
          showIcon
          title={item.error}
          description={
            item.switched.length > 0 && (
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {item.switched.map((s) => (
                  <li key={s.source}>{t('{{label}}：{{reason}}', { label: s.label, reason: s.reason })}</li>
                ))}
              </ul>
            )
          }
        />
      )}
    </Flex>
  )
}

/** Download: the listed files with their sizes and licenses; Advanced chooses one source. */
function DownloadChoice({ setup, busy, onStart }: {
  setup: SetupState
  busy: boolean
  onStart: (files: string[], source: string | null) => void
}) {
  const { t } = useTranslation()
  const [picked, setPicked] = useState<string[]>(() =>
    setup.files.filter((f) => f.default && !f.in_library).map((f) => f.filename),
  )
  const [source, setSource] = useState<string | null>(setup.pinned)
  const size = setup.files.filter((f) => picked.includes(f.filename)).reduce((n, f) => n + f.size, 0)
  const room = setup.free >= size + 1024 ** 3
  const columns = [
    {
      title: '',
      key: 'pick',
      width: 40,
      render: (_: unknown, f: ListedModel) => (
        <Checkbox
          checked={picked.includes(f.filename)}
          disabled={f.in_library}
          onChange={(e) =>
            setPicked((p) => (e.target.checked ? [...p, f.filename] : p.filter((x) => x !== f.filename)))
          }
        />
      ),
    },
    {
      title: t('模型'),
      key: 'name',
      render: (_: unknown, f: ListedModel) => (
        <Flex vertical>
          <Space size={6} wrap>
            <Typography.Text strong>{slotName(f.slot)}</Typography.Text>
            <Tag>{f.slot === 'model' ? t('必需') : t('可选，推荐')}</Tag>
            {f.in_library && <Tag color="success">{t('已导入')}</Tag>}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {f.slot === 'model'
              ? t('从 OCR 文字中抽取字段。')
              : t('照片、扫描件的 OCR 不可靠时，让模型直接看原图。')}{' '}
            {f.filename}
          </Typography.Text>
        </Flex>
      ),
    },
    { title: t('大小'), dataIndex: 'size', width: 110, render: (n: number) => formatBytes(n) },
    { title: t('许可'), dataIndex: 'license', width: 120 },
  ]
  return (
    <Flex vertical gap={12}>
      <Table rowKey="filename" size="small" pagination={false} columns={columns} dataSource={setup.files} />
      <Typography.Text type={room ? 'secondary' : 'danger'}>
        {t('共 {{size}}，数据文件夹所在磁盘可用 {{free}}。', { size: formatBytes(size), free: formatBytes(setup.free) })}
        {!room && t('空间不足：需要再多留出 1 GB。')}
      </Typography.Text>
      <Collapse
        size="small"
        items={[
          {
            key: 'advanced',
            label: t('高级：下载来源'),
            children: (
              <Flex vertical gap={8}>
                <Select<string>
                  style={{ width: 320 }}
                  value={source ?? 'auto'}
                  onChange={(v) => setSource(v === 'auto' ? null : v)}
                  options={[
                    { value: 'auto', label: t('自动选择（推荐）') },
                    ...setup.sources.map((s) => ({ value: s.id, label: s.label })),
                  ]}
                />
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  {t('自动选择时，依次尝试 Hugging Face、魔搭社区和 hf-mirror.com；连不上或 30 秒内几乎收不到数据，就换下一个，并从已下载的位置接着下载。三个来源提供的是同一个文件，下载完都会按 SHA-256 校验。')}
                </Typography.Text>
              </Flex>
            ),
          },
        ]}
      />
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {t('只连接随程序发布的模型清单中的地址，不发送任何个人数据；中途断开或关闭程序后，可以从断点继续。')}
      </Typography.Text>
      <Flex>
        <Button
          type="primary"
          icon={<CloudDownloadOutlined />}
          disabled={picked.length === 0 || !room || busy}
          onClick={() => onStart(picked, source)}
        >
          {t('开始下载')}
        </Button>
      </Flex>
    </Flex>
  )
}

const INBOX_STATUS: Record<InboxFile['status'], { text: (f: InboxFile) => string; color: string }> = {
  copying: { text: () => t('正在复制或下载中'), color: 'processing' },
  waiting: { text: () => t('排队，等前一个文件处理完'), color: 'default' },
  verifying: { text: () => t('正在校验 SHA-256'), color: 'processing' },
  importing: { text: () => t('正在导入'), color: 'processing' },
  in_library: { text: () => t('已导入过，可以删除'), color: 'success' },
  same_size_in_library: { text: () => t('与已导入的推荐模型大小相同，不再校验，可以删除'), color: 'success' },
  busy: { text: () => t('同一文件正在下载或复制，先不处理'), color: 'default' },
  wrong_size: { text: () => t('比清单中的文件大，不是这个文件'), color: 'error' },
  failed: { text: (f) => f.error ?? t('与清单中的文件内容不同'), color: 'error' },
  unknown: { text: () => t('清单外的模型文件：点"导入"选择种类后导入'), color: 'warning' },
  shards_missing: {
    text: (f) => t('分片不全：找到 {{parts}} 个，共 {{count}} 个', { parts: f.parts ?? 0, count: f.count ?? 0 }),
    color: 'warning',
  },
  folder_link: { text: () => t('指向文件夹的链接，不会进入；请放入文件夹本身'), color: 'default' },
}

/** A status this page does not know (an older page with a newer program) is shown as it is. */
function inboxStatus(f: InboxFile): { text: (f: InboxFile) => string; color: string } {
  return (INBOX_STATUS as Partial<typeof INBOX_STATUS>)[f.status] ?? { text: () => f.status, color: 'default' }
}

const GGUF_KINDS: ModelKind[] = ['model', 'mmproj']
const ONNX_KINDS: ModelKind[] = ['ocr_det', 'ocr_rec', 'table']

/** Import one file of the inbox list as it is: kind, model library name and (recognition) dictionary. */
function InboxImport({ file, kinds }: { file: InboxFile; kinds: Record<string, string> }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const onnx = file.name.toLowerCase().endsWith('.onnx')
  const choices = onnx ? ONNX_KINDS : GGUF_KINDS
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<ModelKind | undefined>(choices.find((k) => k === file.kind))
  const [name, setName] = useState(file.suggested ?? '')
  const [dictionary, setDictionary] = useState<string | null>(null)
  const run = useMutation({
    mutationFn: () => importInboxFile(file.name, kind as ModelKind, name.trim(), kind === 'ocr_rec' ? dictionary : null),
    onSuccess: () => {
      setOpen(false)
      if (onnx) void message.success(t('导入完成后，到「设置 → 模型」中换成使用它'))
      void queryClient.invalidateQueries({ queryKey: ['setup'] })
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <>
      <Button size="small" onClick={() => setOpen(true)}>
        {t('导入')}
      </Button>
      <Modal
        open={open}
        title={t('导入 {{name}}', { name: file.name })}
        okText={t('导入')}
        cancelText={t('取消')}
        okButtonProps={{ disabled: !kind || !name.trim(), loading: run.isPending }}
        onOk={() => run.mutate()}
        onCancel={() => setOpen(false)}
        destroyOnHidden
      >
        <Flex vertical gap={12}>
          <Typography.Text type="secondary">
            {onnx
              ? t('ONNX 模型导入后不会自动换上，需要到「设置 → 模型」中选择。')
              : t('导入后设为使用中。')}
          </Typography.Text>
          <Select<ModelKind>
            placeholder={t('选择种类')}
            value={kind}
            onChange={setKind}
            options={choices.map((k) => ({ value: k, label: kinds[k] ?? k }))}
          />
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('模型名称')} maxLength={64} />
          {kind === 'ocr_rec' && (
            <Select<string>
              allowClear
              placeholder={t('识别字典（可选）：模型已内置字符表时不需要')}
              value={dictionary ?? undefined}
              onChange={(v) => setDictionary(v ?? null)}
              options={file.dictionaries.map((d) => ({ value: d, label: d }))}
              notFoundContent={t('同一文件夹中没有 .txt 文件')}
            />
          )}
        </Flex>
      </Modal>
    </>
  )
}

/** Delete inbox files, after a confirmation that says what goes (links: only the link). all: the button for
 * every file already imported. */
function InboxDelete({ files, all = false }: { files: InboxFile[]; all?: boolean }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const run = useMutation({
    mutationFn: () => deleteInboxFiles(files.map((f) => f.name)),
    onSuccess: (deleted) => {
      void message.success(t('已删除 {{count}} 项', { count: deleted.length }))
      void queryClient.invalidateQueries({ queryKey: ['setup'] })
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const links = files.filter((f) => f.link)
  const plain = files.filter((f) => !f.link)
  // A split model's row stands for all its files.
  const plainFiles = plain.reduce((n, f) => n + (f.parts ?? 1), 0)
  const one = files[0]
  let lines: string[]
  if (!all && one.link) {
    lines = [t('仅断开链接，文件本身不会被删除（文件在 {{target}}）。', { target: one.target ?? '' })]
  } else if (!all) {
    lines = [
      one.parts !== null
        ? t('这个分片模型的 {{count}} 个文件都会被直接删除，不进废纸篓，无法恢复。', { count: one.parts })
        : t('文件会被直接删除，不进废纸篓，无法恢复。'),
      t('已导入的模型不受影响。'),
    ]
  } else {
    lines = [
      ...(plainFiles > 0 ? [t('{{count}} 个文件会被直接删除，不进废纸篓，无法恢复。', { count: plainFiles })] : []),
      ...(links.length > 0 ? [t('{{count}} 项仅断开链接，文件本身不会被删除。', { count: links.length })] : []),
      t('已导入的模型不受影响。'),
    ]
  }
  return (
    <Popconfirm
      title={all ? t('删除已导入过的 {{count}} 项？', { count: files.length }) : t('删除 {{name}}？', { name: one.name })}
      description={
        <Flex vertical gap={2} style={{ maxWidth: 380 }}>
          {lines.map((line) => (
            <span key={line} style={{ wordBreak: 'break-all' }}>
              {line}
            </span>
          ))}
        </Flex>
      }
      okText={t('删除')}
      cancelText={t('取消')}
      okButtonProps={{ danger: true, loading: run.isPending }}
      onConfirm={() => run.mutate()}
    >
      {all ? (
        <Button danger icon={<DeleteOutlined />} style={{ width: 'fit-content' }}>
          {t('删除已导入过的（{{count}}）', { count: files.length })}
        </Button>
      ) : (
        <Button size="small" danger icon={<DeleteOutlined />}>
          {t('删除')}
        </Button>
      )}
    </Popconfirm>
  )
}

/** Inbox: files downloaded elsewhere are placed in a folder, at any depth; listed files are imported by
 * themselves, other model files when asked. */
function InboxChoice({ setup }: { setup: SetupState }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const imported = setup.inbox_files.filter(
    (f) => f.deletable && (f.status === 'in_library' || f.status === 'same_size_in_library'),
  )
  return (
    <Flex vertical gap={12}>
      <Typography.Text>
        {t('用浏览器或其他工具下载下面的文件，放进这个文件夹。文件完整后，程序会自动校验、导入并设为使用中。')}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {t('不需要手动确认：这个页面开着时，每 2 秒检查一次文件夹；文件大小与清单一致、且两次检查之间没有变化，就开始校验 SHA-256，通过后移到数据文件夹的 models/hub/，并显示在上方"进度"中。')}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {t('可以直接放入整个下载好的文件夹，任意层级的子文件夹都会查找，包括 Hugging Face 的缓存文件夹。浏览器还没下载完的文件（.crdownload、.part、.download）会等它下载完。清单外的 .gguf、.onnx 文件，以及直接放在 models、models/ocr、models/table 中的模型文件，点"导入"后导入。')}
      </Typography.Text>
      <Space.Compact style={{ width: '100%' }}>
        <Input readOnly value={setup.inbox} />
        <Button icon={<FolderOpenOutlined />} onClick={() => openInbox().catch((e: unknown) => void message.error(errorText(e)))}>
          {t('打开文件夹')}
        </Button>
      </Space.Compact>
      <Table
        rowKey="filename"
        size="small"
        pagination={false}
        dataSource={setup.files}
        columns={[
          { title: t('需要的文件名'), dataIndex: 'filename', render: (v: string) => <Typography.Text copyable>{v}</Typography.Text> },
          { title: t('大小'), dataIndex: 'size', width: 110, render: (n: number) => formatBytes(n) },
          { title: '', dataIndex: 'slot', width: 120, render: (s: string) => slotName(s) },
        ]}
      />
      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
        {t('下载地址：Hugging Face 或魔搭社区上的 unsloth/Qwen3.5-9B-GGUF 仓库。改过名的文件也能认出：大小与清单相同时按 SHA-256 核对。')}
      </Typography.Text>
      {setup.inbox_files.length > 0 && (
        <Table
          rowKey="name"
          size="small"
          pagination={false}
          dataSource={setup.inbox_files}
          columns={[
            {
              title: t('找到的文件（models 文件夹中的位置）'),
              dataIndex: 'name',
              render: (v: string, f: InboxFile) => (
                <Flex vertical gap={2}>
                  <Typography.Text style={{ wordBreak: 'break-all' }}>{v}</Typography.Text>
                  {f.link && f.target && (
                    <Typography.Text type="secondary" style={{ fontSize: 12, wordBreak: 'break-all' }}>
                      {t('实际文件：{{target}}', { target: f.target })}
                    </Typography.Text>
                  )}
                  {f.kind && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{setup.kinds[f.kind] ?? f.kind}</Typography.Text>}
                </Flex>
              ),
            },
            {
              title: t('大小'),
              key: 'size',
              width: 150,
              render: (_: unknown, f: InboxFile) =>
                f.expected ? `${formatBytes(f.size)} / ${formatBytes(f.expected)}` : formatBytes(f.size),
            },
            {
              title: t('状态'),
              dataIndex: 'status',
              width: 300,
              render: (_: unknown, f: InboxFile) => (
                <Flex vertical gap={2}>
                  <Tag color={inboxStatus(f).color} style={{ whiteSpace: 'normal', width: 'fit-content' }}>
                    {f.legacy && f.status === 'unknown' ? t('不在放入文件夹中，程序不会使用：可以导入或删除') : inboxStatus(f).text(f)}
                  </Tag>
                  {f.done !== null && f.total !== null && f.total > 0 && (
                    <Progress size="small" percent={Math.floor((f.done / f.total) * 100)} status="active" />
                  )}
                </Flex>
              ),
            },
            {
              title: '',
              key: 'actions',
              width: 170,
              render: (_: unknown, f: InboxFile) => (
                <Space size={4}>
                  {f.importable && <InboxImport key={f.name} file={f} kinds={setup.kinds} />}
                  {f.deletable && <InboxDelete files={[f]} />}
                </Space>
              ),
            },
          ]}
        />
      )}
      {imported.length > 0 && <InboxDelete files={imported} all />}
      {setup.inbox_other > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {t('另有 {{count}} 个其他文件（不是模型文件），已忽略。', { count: setup.inbox_other })}
        </Typography.Text>
      )}
    </Flex>
  )
}

/** A model file on this computer: copied into the model library (the original stays). */
function FileChoice({ busy }: { busy: boolean }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [path, setPath] = useState('')
  const add = useMutation({
    mutationFn: () => importModelFile(path.trim()),
    onSuccess: () => {
      setPath('')
      void queryClient.invalidateQueries({ queryKey: ['setup'] })
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const pick = async () => {
    try {
      const chosen = await chooseModelFile()
      if (chosen) setPath(chosen)
    } catch (err) {
      void message.error(errorText(err))
    }
  }
  return (
    <Flex vertical gap={12}>
      <Typography.Text>
        {t('已经下载好的 .gguf 模型文件：复制一份导入（原文件保留）并设为使用中。清单中的文件会按清单校验；其他文件作为自定义模型导入，文件名含 mmproj 的作为视觉模型。')}
      </Typography.Text>
      <Space.Compact style={{ width: '100%' }}>
        <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder={t('模型文件的完整路径')} />
        <Button icon={<FolderOpenOutlined />} onClick={() => void pick()}>
          {t('选择…')}
        </Button>
      </Space.Compact>
      <Flex>
        <Button type="primary" disabled={!path.trim() || busy} loading={add.isPending} onClick={() => add.mutate()}>
          {t('导入')}
        </Button>
      </Flex>
    </Flex>
  )
}

/**
 * First-start wizard (desktop app): shown while no large model is found, and from Settings → Models. Three ways
 * to get the models: download them here, place them in the inbox folder, or choose a file on this computer.
 * Restoring a backup (another computer) is in Settings → Data and backup.
 */
export function SetupWizard() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [way, setWay] = useState<Way>('download')
  const setup = useQuery({
    queryKey: ['setup', way === 'inbox'],
    queryFn: () => getSetup(way === 'inbox'),
    refetchInterval: (q) => (q.state.data?.running ? 1000 : way === 'inbox' ? 2000 : false),
  })
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['setup'] })
  const act = (fn: () => Promise<void>) => fn().then(refresh).catch((err: unknown) => void message.error(errorText(err)))
  // The model server still runs the earlier model (or none): offer to start the new one.
  const models = useQuery({ queryKey: ['models'], queryFn: getModels })
  // Each file put to use changes the model selection: read it again.
  const importedCount = (setup.data?.items ?? []).filter((i) => i.state === 'done').length
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ['models'] })
  }, [importedCount, queryClient])
  const job = useMaintenanceJob(() => {
    void queryClient.invalidateQueries()
    void navigate('/dashboard')
  })

  const data = setup.data
  if (!data) {
    return <Card loading={setup.isPending}>{setup.error && <Alert type="error" showIcon title={errorText(setup.error)} />}</Card>
  }
  const items = data.items
  const active = items.some((i) => ['waiting', 'downloading', 'verifying', 'importing'].includes(i.state))
  const resumable = items.some((i) => i.state === 'paused' || i.state === 'failed')
  const imported = items.some((i) => i.state === 'done' && i.imported)
  const ready = !data.needs_model && !active

  return (
    <Card
      title={t('准备识别模型')}
      extra={
        data.needs_model && (
          <Button
            type="link"
            onClick={() => {
              postponeSetup()
              void navigate('/dashboard')
            }}
          >
            {t('稍后再说')}
          </Button>
        )
      }
    >
      <Flex vertical gap={16} style={{ maxWidth: 920 }}>
        {data.model_missing && (
          <Alert
            type="warning"
            showIcon
            title={t('JACARA_MODEL 设置的模型文件不存在：{{path}}', { path: data.model_missing })}
            description={t('环境变量 JACARA_MODEL、JACARA_MMPROJ 优先于这里的选择。请改正或去掉这两项，然后重新启动程序。')}
          />
        )}
        {data.needs_model ? (
          <Alert
            type="info"
            showIcon
            title={t('识别单据需要先准备模型，只需一次')}
            description={t('大模型（约 5.7 GB）从 OCR 文字中抽取字段，必需；视觉模型（约 0.9 GB）在照片、扫描件的 OCR 不可靠时看原图，可选但推荐。模型下载后完全离线运行。')}
          />
        ) : (
          <Alert
            type="success"
            showIcon
            title={t('正在使用的大模型：{{model}}', { model: data.model })}
            description={data.mmproj ? t('视觉模型：{{mmproj}}', { mmproj: data.mmproj }) : t('未使用视觉模型')}
          />
        )}

        {items.length > 0 && (
          <Card size="small" title={t('进度')}
            extra={
              <Space>
                {active && (
                  <Button size="small" icon={<PauseCircleOutlined />} onClick={() => void act(pauseDownloads)}>
                    {t('暂停')}
                  </Button>
                )}
                {!active && resumable && (
                  <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => void act(resumeDownloads)}>
                    {items.some((i) => i.state === 'failed') ? t('重试') : t('继续')}
                  </Button>
                )}
                {(active || resumable) && (
                  <Popconfirm
                    title={t('取消下载？')}
                    description={t('已下载的部分会被删除；已导入的文件保留。')}
                    okText={t('取消下载')}
                    cancelText={t('返回')}
                    onConfirm={() => void act(cancelDownloads)}
                  >
                    <Button size="small" danger>
                      {t('取消')}
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            }
          >
            {items.map((item) => (
              <ItemRow key={`${item.filename}:${item.rel ?? ''}`} item={item} setup={data} />
            ))}
            {items.some((i) => i.state === 'failed' && i.origin === 'download') && (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {t('网络无法连接这些来源时，可以在其他电脑上下载后，用"放入文件夹"导入。')}
              </Typography.Text>
            )}
          </Card>
        )}

        {ready && imported && models.data?.restart_needed && (
          <Result
            icon={<CheckCircleFilled style={{ color: '#52c41a' }} />}
            title={t('模型已就绪')}
            subTitle={t('启动模型服务后就可以导入单据了。')}
            extra={
              <Button type="primary" size="large" loading={job.busy} onClick={() => void job.run(restartServices)}>
                {t('启动模型并开始使用')}
              </Button>
            }
          />
        )}

        <Segmented<Way>
          value={way}
          onChange={setWay}
          options={[
            { value: 'download', icon: <CloudDownloadOutlined />, label: t('在程序内下载（推荐）') },
            { value: 'inbox', icon: <InboxOutlined />, label: t('放入文件夹') },
            { value: 'file', icon: <FolderOpenOutlined />, label: t('选择电脑上的文件') },
          ]}
        />
        {way === 'download' && (
          <DownloadChoice
            key={data.files.map((f) => `${f.filename}:${f.in_library}`).join()}
            setup={data}
            busy={active}
            onStart={(files, source) => void act(() => startDownloads(files, source))}
          />
        )}
        {way === 'inbox' && <InboxChoice setup={data} />}
        {way === 'file' && <FileChoice busy={active} />}

        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {t('换了电脑、有之前的备份？')}{' '}
          <Typography.Link onClick={() => void navigate('/settings/data')}>{t('从备份恢复')}</Typography.Link>
        </Typography.Text>
      </Flex>
      {job.dialog}
    </Card>
  )
}
