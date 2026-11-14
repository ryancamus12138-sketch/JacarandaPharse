import { WarningOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Empty, Flex, Popover, Table, type TableProps, Tag, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { type ExportRecord, listExports } from '../../api/ocrSamples'
import { formatTime } from '../../utils/format'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const PARTS = { get train() { return t('训练') }, get val() { return t('验证') }, get test() { return t('测试') } } as const

/** Export history: groups and lines of each export; problems found after export are recorded here. */
export function ExportsTab() {
  const { t } = useTranslation()
  const exports = useQuery({ queryKey: ['ocr-exports'], queryFn: listExports })
  const columns: TableProps<ExportRecord>['columns'] = [
    {
      title: t('时间'),
      dataIndex: 'created_at',
      width: 170,
      render: (t: string) => formatTime(t),
    },
    {
      title: t('导出内容'),
      dataIndex: 'kind',
      width: 140,
      render: (kind: ExportRecord['kind']) =>
        ({
          train_val: <Tag color="blue">{t('训练和验证')}</Tag>,
          test: <Tag color="gold">{t('锁定测试集')}</Tag>,
          det: <Tag color="purple">{t('检测：训练和验证')}</Tag>,
          det_test: <Tag color="gold">{t('检测：锁定测试集')}</Tag>,
        })[kind],
    },
    { title: t('划分版本'), dataIndex: 'split_version', width: 90, align: 'right' },
    {
      title: t('内容'),
      key: 'counts',
      render: (_, r) => (
        <Typography.Text>
          {(Object.keys(PARTS) as (keyof typeof PARTS)[])
            .filter((p) => r.counts[p])
            .map((p) => t('{{value}} {{value2}} 组 {{value3}} 行', { value: PARTS[p], value2: r.counts[p]?.groups ?? 0, value3: r.counts[p]?.rows ?? 0 }))
            .join(t('；'))}
        </Typography.Text>
      ),
    },
    {
      title: t('导出后发现的问题'),
      key: 'affected',
      width: 220,
      render: (_, r) =>
        r.affected.length === 0 ? (
          <Typography.Text type="secondary">{t('无')}</Typography.Text>
        ) : (
          <Popover
            title={t('本次导出中受影响的分组')}
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
              {t('{{length}} 个问题', { length: r.affected.length })}
            </Tag>
          </Popover>
        ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Typography.Text type="secondary">
        {t('每次导出均记录所含样本、标签版本及所属划分。若之后发现同一单据的 PDF 与照片位于不同划分（或因配对错误被拆分），将在受影响的导出上标注：使用该导出训练的模型，不能再以另一划分中的这些样本作为独立验证。')}
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
        locale={{ emptyText: <Empty description={t('暂无导出记录')} /> }}
      />
    </Flex>
  )
}
