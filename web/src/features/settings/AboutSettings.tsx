import { CheckCircleFilled, CloseCircleFilled, CopyOutlined, FolderOpenOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Card, Collapse, Descriptions, Flex, Input, List, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { openFolder } from '../../api/desktop'
import { getDiagnostics, getLicenseText, getSystemInfo } from '../../api/system'
import type { SystemInfo } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { useDesktop } from '../../desktop'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const VISION_MODE: Record<string, string> = {
  get auto() { return t('仅在照片、扫描件的 OCR 结果不可靠时使用') },
  get always() { return t('每张都使用') },
  get off() { return t('已关闭') },
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

type Process = NonNullable<SystemInfo['desktop']>['processes'][number]

const PROCESS_NAME: Record<string, () => string> = {
  model: () => t('模型服务（llama-server）'),
  'worker-read': () => t('识别进程（OCR）'),
  'worker-extract': () => t('字段抽取进程（大模型）'),
  'worker-all': () => t('处理进程'),
  watch: () => t('监视文件夹进程'),
}

const PROCESS_STATE: Record<Process['state'], { text: () => string; color: string }> = {
  running: { text: () => t('运行中'), color: 'success' },
  restarting: { text: () => t('正在重新启动'), color: 'processing' },
  paused: { text: () => t('已暂停（数据操作进行中）'), color: 'warning' },
  stopped: { text: () => t('已停止'), color: 'error' },
}

/** LICENSE, NOTICE or the third-party license list, shown in a dialog. */
function LicenseLink({ which, title }: { which: 'license' | 'third_party'; title: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const text = useQuery({ queryKey: ['license', which], queryFn: () => getLicenseText(which), enabled: open })
  return (
    <>
      <Typography.Link onClick={() => setOpen(true)}>{t('查看')}</Typography.Link>
      <Modal open={open} title={title} footer={null} width={760} onCancel={() => setOpen(false)}>
        {text.error ? (
          <Alert type="error" showIcon title={errorText(text.error)} />
        ) : (
          <pre style={{ maxHeight: '60vh', overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>
            {text.isPending ? t('正在读取…') : text.data}
          </pre>
        )}
      </Modal>
    </>
  )
}

/**
 * Copy diagnostic information for a problem report, as Obsidian's "Show debug info" offers. When the
 * clipboard cannot be written, the text is shown to copy by hand.
 */
function CopyDiagnostics() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [shown, setShown] = useState<string | null>(null)
  const copy = useMutation({
    mutationFn: getDiagnostics,
    onSuccess: async (text) => {
      try {
        await navigator.clipboard.writeText(text)
        void message.success(t('已复制诊断信息，可以粘贴到问题反馈中'))
      } catch {
        setShown(text)
      }
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <>
      <Button icon={<CopyOutlined />} loading={copy.isPending} onClick={() => copy.mutate()}>
        {t('复制诊断信息')}
      </Button>
      <Modal open={shown !== null} title={t('诊断信息')} footer={null} width={760} onCancel={() => setShown(null)}>
        <Flex vertical gap={8}>
          <Typography.Text type="secondary">{t('无法写入剪贴板，请全选下面的文字后复制。')}</Typography.Text>
          <Input.TextArea readOnly value={shown ?? ''} autoSize={{ minRows: 10, maxRows: 24 }} />
        </Flex>
      </Modal>
    </>
  )
}

/** A folder of the desktop app with a button that opens it in Finder or File Explorer. */
function FolderRow({ path, which }: { path: string; which: 'data' | 'logs' }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  return (
    <Space wrap>
      <Typography.Text copyable style={{ wordBreak: 'break-all' }}>
        {path}
      </Typography.Text>
      <Button size="small" icon={<FolderOpenOutlined />} onClick={() => openFolder(which).catch((e: unknown) => void message.error(errorText(e)))}>
        {t('打开')}
      </Button>
    </Space>
  )
}

/**
 * Settings → About and diagnostics: version, attribution and licenses, folders, background processes and
 * service status; administrators can copy diagnostic information for a problem report.
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
      <Card title={t('关于')} size="small" loading={info.isPending} extra={admin && <CopyDiagnostics />}>
        {d && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="JacarandaParse">v{d.version}</Descriptions.Item>
            <Descriptions.Item label={t('版本')}>{d.mode === 'team' ? t('团队版') : t('个人版（仅限本机使用，无需登录）')}</Descriptions.Item>
            {d.notice && (
              <Descriptions.Item label={t('署名')}>
                <span style={{ whiteSpace: 'pre-wrap' }}>{d.notice}</span>
              </Descriptions.Item>
            )}
            <Descriptions.Item label={t('许可')}>
              <Space>
                {d.license === 'Apache-2.0' ? 'Apache License 2.0' : d.license}
                <LicenseLink which="license" title={t('许可')} />
              </Space>
            </Descriptions.Item>
            <Descriptions.Item label={t('第三方软件许可')}>
              {d.third_party ? (
                <LicenseLink which="third_party" title={t('第三方软件许可')} />
              ) : (
                <Typography.Text type="secondary">{t('随安装包提供（从源码运行时没有）')}</Typography.Text>
              )}
            </Descriptions.Item>
            <Descriptions.Item label={t('当前大模型')}>
              {d.model.loaded ?? <Typography.Text type="secondary">{t('无法连接模型服务')}</Typography.Text>}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      {desktop && d?.desktop && (
        <Card title={t('文件夹')} size="small">
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t('数据文件夹')}>
              <FolderRow path={d.desktop.data_dir} which="data" />
            </Descriptions.Item>
            <Descriptions.Item label={t('日志文件夹')}>
              <FolderRow path={d.desktop.log_dir} which="logs" />
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}
      {desktop && d?.desktop && (
        <Card title={t('后台进程')} size="small">
          <List
            dataSource={d.desktop.processes}
            locale={{ emptyText: t('后台进程没有运行') }}
            renderItem={(p) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <Space wrap>
                      {(PROCESS_NAME[p.name] ?? (() => p.name))()}
                      <Tag color={PROCESS_STATE[p.state].color}>{PROCESS_STATE[p.state].text()}</Tag>
                      {p.starts > 1 && (
                        <Typography.Text type="secondary">{t('已重新启动 {{count}} 次', { count: p.starts - 1 })}</Typography.Text>
                      )}
                    </Space>
                  }
                  description={p.state !== 'running' && p.note ? p.note : undefined}
                />
              </List.Item>
            )}
          />
        </Card>
      )}
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
