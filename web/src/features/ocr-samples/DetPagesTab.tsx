import { DatabaseOutlined, DownloadOutlined, EditOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Empty, Flex, Input, Modal, Radio, Segmented, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import {
  DET_STATUS,
  type DetPageItem,
  type DetStatus,
  downloadDetPages,
  type ExportKind,
  listDetPages,
  previewDetExport,
  saveDetPagesAsDataset,
} from '../../api/ocrSamples'
import { saveFile } from '../../utils/download'
import { formatTime } from '../../utils/format'

const PAGE_SIZE = 20

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function DetExportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<ExportKind>('train_val')
  const [name, setName] = useState('')
  const preview = useQuery({ queryKey: ['det-preview', kind], queryFn: () => previewDetExport(kind), enabled: open })
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ['ocr-exports'] })
    void queryClient.invalidateQueries({ queryKey: ['hub-datasets'] })
    onClose()
  }
  const download = useMutation({
    mutationFn: () => downloadDetPages(kind),
    onSuccess: ({ blob, filename }) => {
      saveFile(blob, filename)
      done()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const store = useMutation({
    mutationFn: () => saveDetPagesAsDataset(kind, { name: name.trim(), description: '检测样本导出（ADR 0061）' }),
    onSuccess: (d) => {
      void message.success(`已存进数据集库：${d.name} v${d.version}`)
      done()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = preview.data
  const names = { train: '训练集', val: '验证集', test: '锁定测试集' }
  return (
    <Modal
      title="导出检测样本"
      open={open}
      onCancel={onClose}
      width={560}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button
          key="dataset"
          icon={<DatabaseOutlined />}
          disabled={!data?.included || !name.trim()}
          loading={store.isPending}
          onClick={() => store.mutate()}
        >
          存进数据集库
        </Button>,
        <Button
          key="zip"
          type="primary"
          icon={<DownloadOutlined />}
          disabled={!data?.included}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          下载 ZIP
        </Button>,
      ]}
    >
      <Flex vertical gap={12}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={kind}
          onChange={(e) => setKind(e.target.value as ExportKind)}
          options={[
            { value: 'train_val', label: '训练集和验证集' },
            { value: 'test', label: '锁定测试集' },
          ]}
        />
        {data && (
          <>
            <Typography.Text>
              确认过的 {data.included} 页、{data.boxes} 个框会导出（划分版本 {data.split_version}）：
              {Object.entries(data.splits)
                .map(([k, v]) => `${names[k as keyof typeof names]} ${v.groups} 组 ${v.rows} 页`)
                .join('，')}
            </Typography.Text>
            {Object.keys(data.excluded).length > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                不导出：{Object.entries(data.excluded).map(([r, n]) => `${r} ${n} 页`).join('；')}
              </Typography.Text>
            )}
          </>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          ZIP 里是 images/（整页图）和 det_gt_train.txt、det_gt_val.txt（每行：图片路径、Tab、框的 JSON），
          PaddleOCR 检测训练直接读。按业务单据分边，同一张单据的页全在同一边。
        </Typography.Text>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="存进数据集库时的名字，例如 收据检测" maxLength={64} />
      </Flex>
    </Modal>
  )
}

/** 检测样本（ADR 0061）：哪些页可以做、做到哪了；点开进框编辑器。 */
export function DetPagesTab({ canExport }: { canExport: boolean }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<DetStatus | ''>('')
  const [pageNo, setPageNo] = useState(1)
  const [exporting, setExporting] = useState(false)
  const list = useQuery({
    queryKey: ['det-pages', status, pageNo],
    queryFn: () => listDetPages(status, (pageNo - 1) * PAGE_SIZE, PAGE_SIZE),
    placeholderData: keepPreviousData,
  })
  const counts = list.data?.counts ?? {}
  const columns: TableProps<DetPageItem>['columns'] = [
    {
      title: '文件',
      key: 'file',
      render: (_, p) => (
        <Link to={`/documents/${p.document_id}`}>
          {p.filename} · 第 {p.page} 页
        </Link>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 110,
      render: (_, p) => <Tag color={DET_STATUS[p.status].color}>{DET_STATUS[p.status].name}</Tag>,
    },
    { title: '框', dataIndex: 'boxes', width: 80, align: 'right' },
    {
      title: '确认时间',
      key: 'confirmed',
      width: 160,
      render: (_, p) => (p.confirmed_at ? formatTime(p.confirmed_at) : ''),
    },
    {
      title: '',
      key: 'open',
      width: 110,
      render: (_, p) => (
        <Button size="small" icon={<EditOutlined />} onClick={() => navigate(`/ocr-samples/det/${p.document_id}/${p.page}`)}>
          {p.status === 'none' ? '开始调框' : '打开'}
        </Button>
      ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        给文字检测模型用的训练数据：整页图和每一行字的框。照 PaddleOCR 的 PPOCRLabel 的做法，先用 OCR 的结果预标注
        （审核时改对的原文、框选的漏认也带上），人逐页调整、确认，只有确认过的导出。只有审核完成的单据、OCR 认的页；
        页上有敏感字段的不做（整页图会把它带出去）。
      </Typography.Text>
      {(counts.sensitive ?? 0) > 0 && (
        <Alert type="info" showIcon title={`${counts.sensitive} 页上有敏感字段，没有列出来。`} />
      )}
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Segmented
          value={status}
          onChange={(v) => {
            setStatus(v as DetStatus | '')
            setPageNo(1)
          }}
          options={[
            { value: '', label: '全部' },
            ...(['none', 'draft', 'confirmed', 'excluded'] as DetStatus[]).map((s) => ({
              value: s,
              label: `${DET_STATUS[s].name} ${counts[s] ?? 0}`,
            })),
          ]}
        />
        {canExport && (
          <Button type="primary" icon={<DownloadOutlined />} disabled={!counts.confirmed} onClick={() => setExporting(true)}>
            导出
          </Button>
        )}
      </Flex>
      {list.isError && <Alert type="error" showIcon title="读不出来" description={errorText(list.error)} />}
      <Table
        rowKey={(p) => `${p.document_id}:${p.page}`}
        size="middle"
        loading={list.isLoading}
        columns={columns}
        dataSource={list.data?.items ?? []}
        pagination={{
          current: pageNo,
          pageSize: PAGE_SIZE,
          total: list.data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (n) => `共 ${n} 页`,
          onChange: setPageNo,
        }}
        locale={{ emptyText: <Empty description="还没有审核完成的单据" /> }}
      />
      <DetExportDialog open={exporting} onClose={() => setExporting(false)} />
    </Flex>
  )
}
