import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, Flex, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { formatBytes } from '../../utils/format'

/** 设置 → 数据和备份（ADR 0063）：有多少数据、怎么备份和恢复（ADR 0054）。 */
export function DataSettings() {
  const info = useQuery({ queryKey: ['system'], queryFn: getSystemInfo })
  const d = info.data
  return (
    <Flex vertical gap={16}>
      {info.error && (
        <Alert type="error" showIcon title={info.error instanceof ApiError ? info.error.message : String(info.error)} />
      )}
      <Card title="数据量" size="small" loading={info.isPending}>
        {d && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="文档">
              {d.data.documents} 份，{d.data.pages} 页
            </Descriptions.Item>
            <Descriptions.Item label="原始文件">{formatBytes(d.data.document_bytes)}</Descriptions.Item>
            <Descriptions.Item label="抽取的字段">{d.data.fields} 个</Descriptions.Item>
            <Descriptions.Item label="人工审核记录">{d.data.revisions} 条</Descriptions.Item>
            <Descriptions.Item label="导出">
              {d.data.exports} 次，存了 {formatBytes(d.data.export_bytes)}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      <Card title="备份和恢复" size="small">
        <Flex vertical gap={8}>
          <Typography.Text>
            备份会把数据库每张表和对象存储里每个文件（原文件、OCR 样本、数据集……）打进一个包，放在项目的 backups/ 文件夹。
            模型文件（models/）和本机的设置（data/settings.json）不在里面，自己另外拷。
          </Typography.Text>
          <Typography.Text>1. 先停下：</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara stop
          </Typography.Text>
          <Typography.Text>2. 备份：</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara backup
          </Typography.Text>
          <Typography.Text>3. 恢复（只能恢复到空的环境里，恢复完会逐样核对）：</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara restore backups/备份包.tar
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            备份包里是你的业务数据，不要放进代码仓库、不要传到公开的地方。
          </Typography.Text>
        </Flex>
      </Card>
    </Flex>
  )
}
