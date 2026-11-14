import { CheckCircleFilled, CloseCircleFilled, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Descriptions, Flex, List, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { useAuth } from '../../auth/useAuth'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const VISION_MODE: Record<string, string> = {
  get auto() { return t('照片、扫描件 OCR 不可靠时才看') },
  get always() { return t('每张都看') },
  get off() { return t('关掉了') },
}

/**
 * Settings → About and diagnostics: version and service status.
 * Details such as the model server address and file names are collapsed by default and visible only to
 * administrators (the backend withholds them from others).
 */
export function AboutSettings() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const info = useQuery({ queryKey: ['system'], queryFn: getSystemInfo })
  const d = info.data
  const admin = can('system:manage')
  return (
    <Flex vertical gap={16}>
      {info.error && (
        <Alert type="error" showIcon title={info.error instanceof ApiError ? info.error.message : String(info.error)} />
      )}
      <Card title={t('关于')} size="small" loading={info.isPending}>
        {d && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="JacarandaParse">v{d.version}</Descriptions.Item>
            <Descriptions.Item label={t('版本')}>{d.mode === 'team' ? t('团队版') : t('个人版（只在本机用，不用登录）')}</Descriptions.Item>
            <Descriptions.Item label={t('正在用的大模型')}>
              {d.model.loaded ?? <Typography.Text type="secondary">{t('连不上模型服务')}</Typography.Text>}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      <Card
        title={t('服务状态')}
        size="small"
        extra={
          <Button icon={<ReloadOutlined />} loading={info.isFetching} onClick={() => void info.refetch()}>
            {t('重新检查')}
          </Button>
        }
        loading={info.isPending}
      >
        <List
          dataSource={d?.services ?? []}
          renderItem={(s) => (
            <List.Item>
              <List.Item.Meta
                avatar={
                  s.ok ? (
                    <CheckCircleFilled style={{ color: '#52c41a', fontSize: 18 }} />
                  ) : (
                    <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 18 }} />
                  )
                }
                title={s.name}
                description={
                  <>
                    {s.detail}
                    {s.hint && (
                      <div>
                        <Typography.Text type="secondary">{t('怎么解决：{{hint}}', { hint: s.hint })}</Typography.Text>
                      </div>
                    )}
                  </>
                }
              />
            </List.Item>
          )}
        />
      </Card>
      {admin && d && (
        <Collapse
          items={[
            {
              key: 'details',
              label: t('诊断细节（排查问题时看）'),
              children: (
                <Descriptions column={1} size="small">
                  <Descriptions.Item label={t('模型服务')}>{d.model.server}</Descriptions.Item>
                  <Descriptions.Item label={t('上下文长度')}>
                    {d.model.context ? `${d.model.context.toLocaleString()} token` : '–'}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('大模型文件')}>{d.model.file ?? '–'}</Descriptions.Item>
                  <Descriptions.Item label={t('看原图（视觉模型）')}>
                    {d.model.sees_images
                      ? t('能看图（{{value}}）· {{value2}}', { value: d.model.vision_file ?? '', value2: VISION_MODE[d.model.vision_mode ?? 'auto'] ?? d.model.vision_mode })
                      : d.model.vision_file
                        ? t('放好了 {{vision_file}}，重新 jacara start 后生效', { vision_file: d.model.vision_file })
                        : t('没有视觉模型')}
                  </Descriptions.Item>
                  <Descriptions.Item label="OCR">
                    {d.ocr.engine}
                    {d.ocr.version ? ` ${d.ocr.version}` : ''}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('版面分析')}>{d.structure?.layout ?? t('没开')}</Descriptions.Item>
                  <Descriptions.Item label={t('表格结构')}>{d.structure?.table ?? t('没有表格模型，按文字位置划分')}</Descriptions.Item>
                  <Descriptions.Item label={t('监视文件夹')}>{d.watch_dir ?? t('没有设')}</Descriptions.Item>
                </Descriptions>
              ),
            },
          ]}
        />
      )}
    </Flex>
  )
}
