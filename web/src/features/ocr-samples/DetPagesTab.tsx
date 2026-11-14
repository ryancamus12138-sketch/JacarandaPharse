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
import { useTranslation } from 'react-i18next'

const PAGE_SIZE = 20

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function DetExportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
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
    mutationFn: () => saveDetPagesAsDataset(kind, { name: name.trim(), description: t('检测样本导出') }),
    onSuccess: (d) => {
      void message.success(t('已存进数据集库：{{name}} v{{version}}', { name: d.name, version: d.version }))
      done()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const data = preview.data
  const names = { train: t('训练集'), val: t('验证集'), test: t('锁定测试集') }
  return (
    <Modal
      title={t('导出检测样本')}
      open={open}
      onCancel={onClose}
      width={560}
      footer={[
        <Button key="cancel" onClick={onClose}>
          {t('取消')}
        </Button>,
        <Button
          key="dataset"
          icon={<DatabaseOutlined />}
          disabled={!data?.included || !name.trim()}
          loading={store.isPending}
          onClick={() => store.mutate()}
        >
          {t('存进数据集库')}
        </Button>,
        <Button
          key="zip"
          type="primary"
          icon={<DownloadOutlined />}
          disabled={!data?.included}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          {t('下载 ZIP')}
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
            { value: 'train_val', label: t('训练集和验证集') },
            { value: 'test', label: t('锁定测试集') },
          ]}
        />
        {data && (
          <>
            <Typography.Text>
              {t('确认过的 {{included}} 页、{{boxes}} 个框会导出（划分版本 {{split_version}}）：', { included: data.included, boxes: data.boxes, split_version: data.split_version })}
              {Object.entries(data.splits)
                .map(([k, v]) => t('{{value}} {{groups}} 组 {{rows}} 页', { value: names[k as keyof typeof names], groups: v.groups, rows: v.rows }))
                .join(t('，'))}
            </Typography.Text>
            {Object.keys(data.excluded).length > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {t('不导出：')}{Object.entries(data.excluded).map(([r, n]) => t('{{r}} {{n}} 页', { r, n })).join(t('；'))}
              </Typography.Text>
            )}
          </>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('ZIP 里是 images/（整页图）和 det_gt_train.txt、det_gt_val.txt（每行：图片路径、Tab、框的 JSON）， PaddleOCR 检测训练直接读。按业务单据分边，同一张单据的页全在同一边。')}
        </Typography.Text>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('存进数据集库时的名字，例如 收据检测')} maxLength={64} />
      </Flex>
    </Modal>
  )
}

/** Detection samples: eligible pages and their progress; opening one starts the box editor. */
export function DetPagesTab({ canExport }: { canExport: boolean }) {
  const { t } = useTranslation()
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
      title: t('文件'),
      key: 'file',
      render: (_, p) => (
        <Link to={`/documents/${p.document_id}`}>
          {t('{{filename}} · 第 {{page}} 页', { filename: p.filename, page: p.page })}
        </Link>
      ),
    },
    {
      title: t('状态'),
      key: 'status',
      width: 110,
      render: (_, p) => <Tag color={DET_STATUS[p.status].color}>{DET_STATUS[p.status].name}</Tag>,
    },
    { title: t('框'), dataIndex: 'boxes', width: 80, align: 'right' },
    {
      title: t('确认时间'),
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
          {p.status === 'none' ? t('开始调框') : t('打开')}
        </Button>
      ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        {t('给文字检测模型用的训练数据：整页图和每一行字的框。照 PaddleOCR 的 PPOCRLabel 的做法，先用 OCR 的结果预标注 （审核时改对的原文、框选的漏认也带上），人逐页调整、确认，只有确认过的导出。只有审核完成的单据、OCR 认的页； 页上有敏感字段的不做（整页图会把它带出去）。')}
      </Typography.Text>
      {(counts.sensitive ?? 0) > 0 && (
        <Alert type="info" showIcon title={t('{{sensitive}} 页上有敏感字段，没有列出来。', { sensitive: counts.sensitive })} />
      )}
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Segmented
          value={status}
          onChange={(v) => {
            setStatus(v as DetStatus | '')
            setPageNo(1)
          }}
          options={[
            { value: '', label: t('全部') },
            ...(['none', 'draft', 'confirmed', 'excluded'] as DetStatus[]).map((s) => ({
              value: s,
              label: `${DET_STATUS[s].name} ${counts[s] ?? 0}`,
            })),
          ]}
        />
        {canExport && (
          <Button type="primary" icon={<DownloadOutlined />} disabled={!counts.confirmed} onClick={() => setExporting(true)}>
            {t('导出')}
          </Button>
        )}
      </Flex>
      {list.isError && <Alert type="error" showIcon title={t('读不出来')} description={errorText(list.error)} />}
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
          showTotal: (n) => t('共 {{n}} 页', { n }),
          onChange: setPageNo,
        }}
        locale={{ emptyText: <Empty description={t('还没有审核完成的单据')} /> }}
      />
      <DetExportDialog open={exporting} onClose={() => setExporting(false)} />
    </Flex>
  )
}
