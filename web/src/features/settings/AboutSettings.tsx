import { CheckCircleFilled, CloseCircleFilled, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Descriptions, Flex, List, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { useAuth } from '../../auth/useAuth'
import { useDesktop } from '../../desktop'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const VISION_MODE: Record<string, string> = {
  get auto() { return t('仅在照片、扫描件的 OCR 结果不可靠时使用') },
  get always() { return t('每张都使用') },
  get off() { return t('已关闭') },
}

/**
 * Settings → About and diagnostics: version and service status.
 * Details such as the model server address and file names are collapsed by default and visible only to
 * administrators (the backend withholds them from others).
 */
export function AboutSettings() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const desktop = useDesktop()
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
            <Descriptions.Item label={t('版本')}>{d.mode === 'team' ? t('团队版') : t('个人版（仅限本机使用，无需登录）')}</Descriptions.Item>
            <Descriptions.Item label={t('当前大模型')}>
              {d.model.loaded ?? <Typography.Text type="secondary">{t('无法连接模型服务')}</Typography.Text>}
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
                        <Typography.Text type="secondary">{t('解决方法：{{hint}}', { hint: s.hint })}</Typography.Text>
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
              label: t('诊断信息（用于排查问题）'),
              children: (
                <Descriptions column={1} size="small">
                  <Descriptions.Item label={t('模型服务')}>{d.model.server}</Descriptions.Item>
                  <Descriptions.Item label={t('上下文长度')}>
                    {d.model.context ? `${d.model.context.toLocaleString()} token` : '–'}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('大模型文件')}>{d.model.file ?? '–'}</Descriptions.Item>
                  <Descriptions.Item label={t('读取原图（视觉模型）')}>
                    {d.model.sees_images
                      ? t('支持读图（{{value}}）· {{value2}}', { value: d.model.vision_file ?? '', value2: VISION_MODE[d.model.vision_mode ?? 'auto'] ?? d.model.vision_mode })
                      : d.model.vision_file
                        ? desktop
                          ? t('已放置 {{vision_file}}，重新启动 JacarandaParse 后生效', { vision_file: d.model.vision_file })
                          : t('已放置 {{vision_file}}，执行 jacara start 重启后生效', { vision_file: d.model.vision_file })
                        : t('没有视觉模型')}
                  </Descriptions.Item>
                  <Descriptions.Item label="OCR">
                    {d.ocr.engine}
                    {d.ocr.version ? ` ${d.ocr.version}` : ''}
                  </Descriptions.Item>
                  <Descriptions.Item label={t('版面分析')}>{d.structure?.layout ?? t('未开启')}</Descriptions.Item>
                  <Descriptions.Item label={t('表格结构')}>{d.structure?.table ?? t('无表格模型，按文字位置划分')}</Descriptions.Item>
                  <Descriptions.Item label={t('监视文件夹')}>{d.watch_dir ?? t('未设置')}</Descriptions.Item>
                </Descriptions>
              ),
            },
          ]}
        />
      )}
    </Flex>
  )
}
