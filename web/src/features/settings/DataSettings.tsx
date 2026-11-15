import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, Flex, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { useDesktop } from '../../desktop'
import { formatBytes } from '../../utils/format'
import { DesktopDataSettings } from './DesktopDataSettings'
import { useTranslation } from 'react-i18next'

/**
 * Settings → Data and backup: data volume, and backing up and restoring. The desktop app does this with
 * buttons (DesktopDataSettings); a deployment from source with the commands shown here.
 */
export function DataSettings() {
  const { t } = useTranslation()
  const desktop = useDesktop()
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
              {t('{{exports}} 次，占用 {{formatBytes}}', { exports: d.data.exports, formatBytes: formatBytes(d.data.export_bytes) })}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      {desktop ? (
        <DesktopDataSettings />
      ) : (
      <Card title={t('备份和恢复')} size="small">
        <Flex vertical gap={8}>
          <Typography.Text>
            {t('备份将数据库全部表及对象存储中的全部文件（原文件、OCR 样本、数据集等）打包，保存在项目的 backups/ 目录。模型文件（models/）和本机设置（data/settings.json）不包含在内，请另行复制。')}
          </Typography.Text>
          <Typography.Text>{t('1. 停止服务：')}</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara stop
          </Typography.Text>
          <Typography.Text>{t('2. 备份：')}</Typography.Text>
          <Typography.Text code copyable>
            uv run jacara backup
          </Typography.Text>
          <Typography.Text>{t('3. 恢复（仅可恢复到空环境，恢复后逐项校验）：')}</Typography.Text>
          <Typography.Text code copyable>
            {t('uv run jacara restore backups/备份包.tar')}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('备份包包含业务数据，请勿提交到代码仓库或上传至公开位置。')}
          </Typography.Text>
        </Flex>
      </Card>
      )}
    </Flex>
  )
}
