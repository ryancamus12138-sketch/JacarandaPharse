import { DownloadOutlined } from '@ant-design/icons'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Card, Popover, Table, type TableProps, Tag, Typography } from 'antd'
import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import { downloadExport, listExports } from '../../api/exports'
import type { ExportRecord } from '../../api/types'
import { saveFile } from '../../utils/download'
import { formatBytes, formatTime } from '../../utils/format'
import { useTranslation } from 'react-i18next'

const PAGE_SIZE = 20

/** Exported documents: the count; hovering lists file names (each opens the review page). */
function DocumentsCell({ record }: { record: ExportRecord }) {
  const { t } = useTranslation()
  const list = (
    <div style={{ maxHeight: 260, overflow: 'auto', maxWidth: 360 }}>
      {record.documents.map((d) => (
        <div key={d.id}>
          <Link to={`/documents/${d.id}`}>{d.filename}</Link>
        </div>
      ))}
    </div>
  )
  return (
    <Popover content={list} title={t('导出的文档')}>
      <Typography.Link>{t('{{document_count}} 份', { document_count: record.document_count })}</Typography.Link>
    </Popover>
  )
}

/**
 * Export history: every export stored a file that can be downloaded again.
 * Re-downloads contain the content as exported at the time; for later changes, export again from the document
 * list.
 */
export function ExportsPage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [page, setPage] = useState(1)
  const exports = useQuery({
    queryKey: ['exports', page],
    queryFn: () => listExports(PAGE_SIZE, (page - 1) * PAGE_SIZE),
    placeholderData: keepPreviousData,
  })
  const download = useMutation({
    mutationFn: (r: ExportRecord) => downloadExport(r.id, r.filename),
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const columns: TableProps<ExportRecord>['columns'] = [
    { title: t('时间'), dataIndex: 'created_at', width: 170, render: formatTime },
    { title: t('文件'), dataIndex: 'filename', ellipsis: true },
    {
      title: t('格式'),
      dataIndex: 'format',
      width: 150,
      render: (f: ExportRecord['format']) =>
        f === 'zip' ? (
          <Tag>{t('每份一个 Excel（zip）')}</Tag>
        ) : f === 'csv' ? (
          <Tag color="cyan">CSV</Tag>
        ) : f === 'csv-zip' ? (
          <Tag color="cyan">{t('CSV（zip）')}</Tag>
        ) : f === 'json' ? (
          <Tag color="geekblue">JSON</Tag>
        ) : (
          <Tag color="purple">{t('汇总 Excel')}</Tag>
        ),
    },
    { title: t('文档'), key: 'documents', width: 90, render: (_, r) => <DocumentsCell record={r} /> },
    { title: t('大小'), dataIndex: 'size_bytes', width: 100, align: 'right', render: formatBytes },
    { title: t('导出人'), dataIndex: 'user_name', width: 120, ellipsis: true },
    {
      title: t('操作'),
      key: 'actions',
      width: 120,
      render: (_, r) =>
        r.available ? (
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={download.isPending && download.variables?.id === r.id}
            onClick={() => download.mutate(r)}
          >
            {t('重新下载')}
          </Button>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('文件没保存')}
          </Typography.Text>
        ),
    },
  ]

  return (
    <Card title={t('导出记录')}>
      <Typography.Paragraph type="secondary">
        {t('每次导出的文件都存了一份。重新下载拿到的是当时的内容；文档后来又改过的话，回文档列表重新导出。')}
      </Typography.Paragraph>
      {exports.error && (
        <Alert
          type="error"
          showIcon
          title={exports.error instanceof ApiError ? exports.error.message : String(exports.error)}
          style={{ marginBottom: 16 }}
        />
      )}
      <Table<ExportRecord>
        rowKey="id"
        columns={columns}
        dataSource={exports.data?.items}
        loading={exports.isPending}
        scroll={{ x: 800 }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: exports.data?.total ?? 0,
          showTotal: (total) => t('共 {{total}} 次', { total }),
          onChange: setPage,
        }}
        locale={{ emptyText: t('还没有导出过。在文档列表勾选文档，点"导出"') }}
      />
    </Card>
  )
}
