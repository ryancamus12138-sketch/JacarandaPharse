import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Flex, Modal, Progress, Spin, Typography } from 'antd'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type BackupResult,
  getJob,
  type Job,
  type MoveResult,
  type RestoreResult,
} from '../../api/desktop'
import { formatBytes } from '../../utils/format'
import { t } from '../../i18n'

const RUNNING: Record<Job['kind'], () => string> = {
  backup: () => t('正在备份'),
  restore: () => t('正在从备份恢复'),
  move: () => t('正在移动数据文件夹'),
  restart: () => t('正在重新启动后台'),
}

const DONE: Record<Job['kind'], () => string> = {
  backup: () => t('备份完成'),
  restore: () => t('已从备份恢复'),
  move: () => t('数据文件夹已移动'),
  restart: () => t('后台已重新启动'),
}

const FAILED: Record<Job['kind'], () => string> = {
  backup: () => t('备份未完成'),
  restore: () => t('恢复未完成'),
  move: () => t('移动未完成'),
  restart: () => t('重新启动未完成'),
}

/** While it runs: restoring and moving hold off every request, a backup and a restart hold off changes. */
function holdText(kind: Job['kind']): string {
  return kind === 'restore' || kind === 'move'
    ? t('期间无法使用 JacarandaParse，请勿关闭程序。')
    : t('期间可以浏览，但不能修改；后台处理暂停，完成后继续。')
}

/** What finished, per kind. Restoring and moving change all data, so the page reloads afterwards. */
function DoneContent({ job }: { job: Job }) {
  const { t } = useTranslation()
  if (job.kind === 'backup') {
    const result = job.result as unknown as BackupResult
    return (
      <Flex vertical gap={4}>
        <Typography.Text>
          {t('{{documents}} 份文档、{{files}} 个文件，共 {{size}}{{models}}。', {
            documents: result.documents,
            files: result.files,
            size: formatBytes(result.size),
            models: result.models ? t('（含模型文件）') : '',
          })}
        </Typography.Text>
        <Typography.Text type="secondary">{t('保存位置：')}</Typography.Text>
        <Typography.Text code copyable>
          {result.path}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {t('备份包含业务数据，请妥善保管，不要上传到公开位置。为防磁盘损坏，建议另存一份到其他磁盘。')}
        </Typography.Text>
      </Flex>
    )
  }
  if (job.kind === 'restore') {
    const result = job.result as unknown as RestoreResult
    const missing = result.models_missing ?? []
    return (
      <Flex vertical gap={8}>
        <Typography.Text>{t('恢复前的数据已移到下面的文件夹，不会自动删除；确认恢复无误后可自行删除：')}</Typography.Text>
        <Typography.Text code copyable>
          {result.aside}
        </Typography.Text>
        {result.kind === 'database' && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('自动备份只包含数据库：原文件、模型和设置保持恢复前的样子。')}
          </Typography.Text>
        )}
        {result.warnings.length > 0 && (
          <Alert
            type="warning"
            showIcon
            title={t('以下文件在备份之后被删除，相关文档的原图无法打开：')}
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            }
          />
        )}
        {missing.length > 0 && (
          <Alert
            type="warning"
            showIcon
            title={t('备份中不包含以下模型文件，本机也没有：')}
            description={
              <Flex vertical gap={4}>
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {missing.map((m) => (
                    <li key={m}>{m}</li>
                  ))}
                </ul>
                <Typography.Text>{t('请在“模型和数据集”中重新下载或导入，或把文件放回数据文件夹的 models/。')}</Typography.Text>
              </Flex>
            }
          />
        )}
      </Flex>
    )
  }
  if (job.kind === 'move') {
    const result = job.result as unknown as MoveResult
    return (
      <Flex vertical gap={4}>
        <Typography.Text>{t('全部数据已复制到新位置并逐个核对，原位置的数据已删除。今后启动时使用：')}</Typography.Text>
        <Typography.Text code copyable>
          {result.path}
        </Typography.Text>
      </Flex>
    )
  }
  return <Typography.Text>{t('新的设置和模型已生效；正在处理的文档会继续处理。')}</Typography.Text>
}

/**
 * Dialog following a maintenance job (GET /desktop/job, which answers during any job) until it ends. It
 * cannot be closed while the job runs.
 */
export function JobDialog({ started, onClose }: { started: Job; onClose: (job: Job) => void }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['desktop-job', started.started],
    queryFn: getJob,
    initialData: started,
    refetchInterval: (q) => (q.state.data?.running === false ? false : 500),
    retry: true,
  })
  // A different job means this one ended and another began; show this one as ended without details.
  const job = query.data && query.data.started === started.started ? query.data : started
  const running = job.running
  const failed = !running && job.error !== null
  const percent = job.total > 0 ? Math.floor((job.done / job.total) * 100) : null
  const reloads = !failed && (job.kind === 'restore' || job.kind === 'move')
  const title = running ? RUNNING[job.kind]() : failed ? FAILED[job.kind]() : DONE[job.kind]()
  let body: ReactNode
  if (running) {
    body = (
      <Flex vertical gap={12}>
        {percent !== null ? (
          <Progress percent={percent} status="active" />
        ) : (
          <Flex justify="center">
            <Spin />
          </Flex>
        )}
        <Typography.Text type="secondary" ellipsis={{ tooltip: job.stage }}>
          {job.stage || t('准备中…')}
        </Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {holdText(job.kind)}
        </Typography.Text>
      </Flex>
    )
  } else if (failed) {
    body = (
      <Flex vertical gap={8}>
        <Alert type="error" showIcon title={job.error} />
        {job.kind !== 'restart' && <Typography.Text>{t('原来的数据保持不变。')}</Typography.Text>}
      </Flex>
    )
  } else {
    body = <DoneContent job={job} />
  }
  return (
    <Modal
      open
      title={title}
      closable={!running}
      mask={{ closable: false }}
      keyboard={!running}
      onCancel={() => !running && onClose(job)}
      footer={
        running
          ? null
          : [
              <Button key="ok" type="primary" onClick={() => onClose(job)}>
                {reloads ? t('完成，重新载入') : t('关闭')}
              </Button>,
            ]
      }
    >
      {body}
    </Modal>
  )
}
