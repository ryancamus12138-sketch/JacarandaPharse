import { useQueryClient } from '@tanstack/react-query'
import { Button, Tooltip } from 'antd'
import { useTranslation } from 'react-i18next'
import { restartServices } from '../../api/desktop'
import { useMaintenanceJob } from './useMaintenanceJob'

/**
 * Desktop app: apply changed settings and models now by restarting the background services (workers, folder
 * watcher, model server). Documents being processed continue afterwards.
 */
export function RestartServicesButton({ inAlert = true }: { inAlert?: boolean }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const job = useMaintenanceJob(() => {
    void queryClient.invalidateQueries({ queryKey: ['app-settings'] })
    void queryClient.invalidateQueries({ queryKey: ['models'] })
  })
  return (
    <>
      <Tooltip title={t('重新启动后台处理、文件夹监视和模型服务，以使用新的设置和模型。正在处理的文档会在重启后继续。')}>
        <Button
          type={inAlert ? 'primary' : 'default'}
          size={inAlert ? 'small' : 'middle'}
          loading={job.busy}
          onClick={() => void job.run(restartServices)}
        >
          {t('重新启动后台')}
        </Button>
      </Tooltip>
      {job.dialog}
    </>
  )
}
