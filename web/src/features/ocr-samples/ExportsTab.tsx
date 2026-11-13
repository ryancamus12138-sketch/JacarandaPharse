import { WarningOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Empty, Flex, Popover, Table, type TableProps, Tag, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { type ExportRecord, listExports } from '../../api/ocrSamples'
import { formatTime } from '../../utils/format'

const PARTS = { train: '训练', val: '验证', test: '测试' } as const

/** 导出记录（ADR 0052）：每次导出了几组几行；导出以后发现的问题记在这里。 */
export function ExportsTab() {
  const exports = useQuery({ queryKey: ['ocr-exports'], queryFn: listExports })
  const columns: TableProps<ExportRecord>['columns'] = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 170,
      render: (t: string) => formatTime(t),
    },
    {
      title: '导出的',
      dataIndex: 'kind',
      width: 140,
      render: (kind: ExportRecord['kind']) =>
        kind === 'test' ? <Tag color="gold">锁定测试集</Tag> : <Tag color="blue">训练和验证</Tag>,
    },
    { title: '划分版本', dataIndex: 'split_version', width: 90, align: 'right' },
    {
      title: '内容',
      key: 'counts',
      render: (_, r) => (
        <Typography.Text>
          {(Object.keys(PARTS) as (keyof typeof PARTS)[])
            .filter((p) => r.counts[p])
            .map((p) => `${PARTS[p]} ${r.counts[p]?.groups ?? 0} 组 ${r.counts[p]?.rows ?? 0} 行`)
            .join('；')}
        </Typography.Text>
      ),
    },
    {
      title: '导出以后发现的问题',
      key: 'affected',
      width: 220,
      render: (_, r) =>
        r.affected.length === 0 ? (
          <Typography.Text type="secondary">没有</Typography.Text>
        ) : (
          <Popover
            title="这次导出受影响的组"
            content={
              <Flex vertical gap={8} style={{ maxWidth: 420 }}>
                {r.affected.map((a) => (
                  <div key={`${a.group}-${a.at}`}>
                    <Typography.Text code>{a.group}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {' '}
                      {formatTime(a.at)}
                    </Typography.Text>
                    <div style={{ fontSize: 13 }}>{a.reason}</div>
                  </div>
                ))}
              </Flex>
            }
          >
            <Tag color="error" icon={<WarningOutlined />} style={{ cursor: 'pointer' }}>
              {r.affected.length} 个问题
            </Tag>
          </Popover>
        ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        每次导出都记下导了哪些样本、哪一版标签、分在哪边。之后如果发现同一张单据的 PDF 和照片分在了两边（或配错拆开），
        会在受影响的导出上记一笔：用那次导出训练的模型，不能再用另一边的样本当独立的验证。
      </Typography.Text>
      {exports.isError && (
        <Alert
          type="error"
          showIcon
          title={exports.error instanceof ApiError ? exports.error.message : String(exports.error)}
        />
      )}
      <Table
        rowKey="id"
        size="middle"
        loading={exports.isLoading}
        columns={columns}
        dataSource={exports.data ?? []}
        pagination={{ pageSize: 20, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{ emptyText: <Empty description="还没有导出过" /> }}
      />
    </Flex>
  )
}
