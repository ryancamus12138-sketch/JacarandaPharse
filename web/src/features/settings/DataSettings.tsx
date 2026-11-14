import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, Flex, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { formatBytes } from '../../utils/format'
import { useTranslation } from 'react-i18next'

/** Settings → Data and backup: data volume, and how to back up and restore. */
export function DataSettings() {
  const { t } = useTranslation()
  const info = useQuery({ queryKey: ['system'], queryFn: getSystemInfo })
  const d = info.data
  return (
    <Flex vertical gap={16}>
      {info.error && (
        <Alert type="error" showIcon title={info.error instanceof ApiError ? info.error.message : String(info.error)} />
      )}
      <Card title={t('数据量')} size="small" loading={info.isPending}>
        {d && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t('文档')}>
              {t('{{documents}} 份，{{pages}} 页', { documents: d.data.documents, pages: d.data.pages })}
            </Descriptions.Item>
            <Descriptions.Item label={t('原始文件')}>{formatBytes(d.data.document_bytes)}</Descriptions.Item>
            <Descriptions.Item label={t('抽取的字段')}>{t('{{fields}} 个', { fields: d.data.fields })}</Descriptions.Item>
            <Descriptions.Item label={t('人工审核记录')}>{t('{{revisions}} 条', { revisions: d.data.revisions })}</Descriptions.Item>
            <Descriptions.Item label={t('导出')}>
              {t('{{exports}} 次，存了 {{formatBytes}}', { exports: d.data.exports, formatBytes: formatBytes(d.data.export_bytes) })}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      <Card title={t('备份和恢复')} size="small">
        <Flex vertical gap={8}>
          <Typography.Text>
            {t('备份会把数据库每张表和对象存储里每个文件（原文件、OCR 样本、数据集……）打进一个包，放在项目的 backups/ 文件夹。 模型文件（models/）和本机的设置（data/settings.json）不在里面，自己另外拷。')}
          </Typography.Text>
          <Typography.Text>{t('1. 先停下：')}</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara stop
          </Typography.Text>
          <Typography.Text>{t('2. 备份：')}</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara backup
          </Typography.Text>
          <Typography.Text>{t('3. 恢复（只能恢复到空的环境里，恢复完会逐样核对）：')}</Typography.Text>
          <Typography.Text code copyable>
            {t('uv run jacara restore backups/备份包.tar')}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('备份包里是你的业务数据，不要放进代码仓库、不要传到公开的地方。')}
          </Typography.Text>
        </Flex>
      </Card>
    </Flex>
  )
}
