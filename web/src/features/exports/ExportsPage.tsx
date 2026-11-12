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

const PAGE_SIZE = 20

/** 导出了哪些文档：数量，鼠标放上去列出文件名（点了进审核页）。 */
function DocumentsCell({ record }: { record: ExportRecord }) {
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
    <Popover content={list} title="导出的文档">
      <Typography.Link>{record.document_count} 份</Typography.Link>
    </Popover>
  )
}

/**
 * 导出记录：每次导出都存了一份文件，可以重新下载。
 * 重新下载拿到的是当时导出的内容；文档后来又改过的话，要回文档列表重新导出。
 */
export function ExportsPage() {
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
    { title: '时间', dataIndex: 'created_at', width: 170, render: formatTime },
    { title: '文件', dataIndex: 'filename', ellipsis: true },
    {
      title: '格式',
      dataIndex: 'format',
      width: 150,
      render: (f: ExportRecord['format']) =>
        f === 'zip' ? <Tag>每份一个 Excel（zip）</Tag> : <Tag color="purple">汇总 Excel</Tag>,
    },
    { title: '文档', key: 'documents', width: 90, render: (_, r) => <DocumentsCell record={r} /> },
    { title: '大小', dataIndex: 'size_bytes', width: 100, align: 'right', render: formatBytes },
    { title: '导出人', dataIndex: 'user_name', width: 120, ellipsis: true },
    {
      title: '操作',
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
            重新下载
          </Button>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            文件没保存
          </Typography.Text>
        ),
    },
  ]

  return (
    <Card title="导出记录">
      <Typography.Paragraph type="secondary">
        每次导出的文件都存了一份。重新下载拿到的是当时的内容；文档后来又改过的话，回文档列表重新导出。
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
          showTotal: (total) => `共 ${total} 次`,
          onChange: setPage,
        }}
        locale={{ emptyText: '还没有导出过。在文档列表勾选文档，点"导出"' }}
      />
    </Card>
  )
}
