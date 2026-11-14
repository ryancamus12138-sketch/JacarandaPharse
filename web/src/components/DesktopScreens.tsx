import { useQuery } from '@tanstack/react-query'
import { App, Button, Flex, Progress, Result, Spin, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../api/client'
import { getJob, getLocation, retryLocation, switchToDefaultLocation } from '../api/desktop'

/**
 * Desktop app opened (or reloaded) while a backup, restore, move or restart holds requests off: follow the job
 * and reload when it ends.
 */
export function MaintenanceWait() {
  const { t } = useTranslation()
  const job = useQuery({ queryKey: ['desktop-job', 'wait'], queryFn: getJob, refetchInterval: 1000, retry: true })
  const finished = job.isSuccess && (job.data === null || !job.data.running)
  useEffect(() => {
    if (finished) {
      window.location.reload()
    }
  }, [finished])
  const data = job.data
  const percent = data && data.total > 0 ? Math.floor((data.done / data.total) * 100) : null
  return (
    <Result
      icon={<Spin size="large" />}
      title={t('正在进行数据操作，请稍候')}
      subTitle={
        <Flex vertical gap={8} align="center">
          {percent !== null && <Progress percent={percent} status="active" style={{ width: 360 }} />}
          {data?.stage && <Typography.Text type="secondary">{data.stage}</Typography.Text>}
          <Typography.Text type="secondary">{t('完成后会自动打开。请勿关闭 JacarandaParse。')}</Typography.Text>
        </Flex>
      }
    />
  )
}

/**
 * Desktop app: the data folder was moved to a place that cannot be found now (an external drive not
 * connected). Nothing is opened until it is found again or the app's own place is chosen.
 */
export function DataFolderMissing({ folder }: { folder: string }) {
  const { t } = useTranslation()
  const { message, modal } = App.useApp()
  const [busy, setBusy] = useState(false)
  const location = useQuery({ queryKey: ['desktop-location'], queryFn: getLocation })
  const fail = (err: unknown) => void message.error(err instanceof ApiError ? err.message : String(err))

  const retry = async () => {
    setBusy(true)
    try {
      await retryLocation()
      window.location.reload()
    } catch (err) {
      fail(err)
      setBusy(false)
    }
  }

  const chooseDefault = () => {
    modal.confirm({
      title: t('改用默认位置？'),
      content: (
        <Flex vertical gap={8}>
          <Typography.Text>{t('将改用 JacarandaParse 自己的数据位置，打开其中现有的数据（移动之前的数据已不在那里，可能是空的）：')}</Typography.Text>
          <Typography.Text code>{location.data?.default ?? ''}</Typography.Text>
          <Typography.Text>
            {t('原来的文件夹不会被删除。之后连接上它时，可以在“设置 → 数据和备份”中从其中 backups/ 里的备份恢复。')}
          </Typography.Text>
        </Flex>
      ),
      okText: t('改用默认位置'),
      cancelText: t('取消'),
      onOk: async () => {
        try {
          await switchToDefaultLocation()
          window.location.reload()
        } catch (err) {
          fail(err)
        }
      },
    })
  }

  return (
    <Result
      status="warning"
      title={t('找不到数据文件夹')}
      subTitle={
        <Flex vertical gap={8} align="center">
          <Typography.Text code copyable>
            {folder}
          </Typography.Text>
          <Typography.Text type="secondary">
            {t('数据文件夹之前被移到了这里。如果它在移动硬盘上，请连接后点“重试”。')}
          </Typography.Text>
        </Flex>
      }
      extra={[
        <Button key="retry" type="primary" loading={busy} onClick={() => void retry()}>
          {t('重试')}
        </Button>,
        <Button key="default" disabled={busy || !location.data} onClick={chooseDefault}>
          {t('改用默认位置')}
        </Button>,
      ]}
    />
  )
}
