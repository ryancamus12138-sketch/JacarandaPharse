import { FolderOpenOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Flex,
  Input,
  Modal,
  Radio,
  Space,
  Table,
  type TableProps,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import {
  type BackupFile,
  checkMove,
  chooseBackupFile,
  chooseFolder,
  getDataInfo,
  openFolder,
  startBackup,
  startMove,
  startRestore,
} from '../../api/desktop'
import { getSettings, updateSettings } from '../../api/settings'
import { useAuth } from '../../auth/useAuth'
import { formatBytes, formatTime } from '../../utils/format'
import { useMaintenanceJob } from './useMaintenanceJob'
import { SettingRow } from './ProcessingSettings'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

/** Seconds since the epoch, as local date and time. */
function timeOf(seconds: number): string {
  return formatTime(new Date(seconds * 1000).toISOString())
}

/** "Back up now": where to save it and whether to include the model files. */
function BackupDialog({ open, onCancel, onStart }: {
  open: boolean
  onCancel: () => void
  onStart: (destination: string | null, models: boolean) => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [where, setWhere] = useState<'data' | 'other'>('data')
  const [folder, setFolder] = useState('')
  const [models, setModels] = useState(false)
  const choose = async () => {
    try {
      const chosen = await chooseFolder('backup')
      if (chosen) {
        setFolder(chosen)
        setWhere('other')
      }
    } catch (err) {
      void message.error(errorText(err))
    }
  }
  const ready = where === 'data' || folder.trim() !== ''
  return (
    <Modal
      open={open}
      title={t('立即备份')}
      okText={t('开始备份')}
      cancelText={t('取消')}
      okButtonProps={{ disabled: !ready }}
      onCancel={onCancel}
      onOk={() => onStart(where === 'other' ? folder.trim() : null, models)}
      destroyOnHidden
    >
      <Flex vertical gap={16}>
        <Typography.Text type="secondary">
          {t('完整备份包含数据库、全部文件（原文件、OCR 样本、数据集等）、设置，以及所用模型的清单（文件名和校验值）。备份期间可以浏览，但不能修改，后台处理暂停。')}
        </Typography.Text>
        <Flex vertical gap={8}>
          <Typography.Text strong>{t('保存位置')}</Typography.Text>
          <Radio.Group value={where} onChange={(e) => setWhere(e.target.value as 'data' | 'other')}>
            <Space orientation="vertical">
              <Radio value="data">{t('数据文件夹中的 backups/（默认）')}</Radio>
              <Radio value="other">{t('其他位置（例如移动硬盘）')}</Radio>
            </Space>
          </Radio.Group>
          {where === 'other' && (
            <Space.Compact style={{ width: '100%' }}>
              <Input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={t('选择或输入文件夹的完整路径')} />
              <Button icon={<FolderOpenOutlined />} onClick={() => void choose()}>
                {t('选择…')}
              </Button>
            </Space.Compact>
          )}
        </Flex>
        <Flex vertical gap={4}>
          <Checkbox checked={models} onChange={(e) => setModels(e.target.checked)}>
            {t('包含模型文件')}
          </Checkbox>
          <Typography.Text type="secondary" style={{ fontSize: 13, paddingLeft: 24 }}>
            {t('模型文件通常有几个 GB。不包含时，恢复后按清单重新下载或导入即可；换电脑且无法联网时建议包含。')}
          </Typography.Text>
        </Flex>
      </Flex>
    </Modal>
  )
}

/**
 * Ask for a path: typed in, or chosen with the system dialog (the page cannot learn a path itself; where the
 * system has no dialog, typing it is the way).
 */
function PathDialog({ open, title, intro, choose, placeholder, okText, onCancel, onOk }: {
  open: boolean
  title: string
  intro: string
  choose: () => Promise<string | null>
  placeholder: string
  okText: string
  onCancel: () => void
  onOk: (path: string) => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [path, setPath] = useState('')
  const pick = async () => {
    try {
      const chosen = await choose()
      if (chosen) {
        setPath(chosen)
      }
    } catch (err) {
      void message.error(errorText(err))
    }
  }
  return (
    <Modal
      open={open}
      title={title}
      okText={okText}
      cancelText={t('取消')}
      okButtonProps={{ disabled: path.trim() === '' }}
      onCancel={onCancel}
      onOk={() => onOk(path.trim())}
      destroyOnHidden
    >
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">{intro}</Typography.Text>
        <Space.Compact style={{ width: '100%' }}>
          <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder={placeholder} />
          <Button icon={<FolderOpenOutlined />} onClick={() => void pick()}>
            {t('选择…')}
          </Button>
        </Space.Compact>
      </Flex>
    </Modal>
  )
}

/**
 * Settings → Data and backups in the desktop app: where the data folder is (and moving it), backing up with a
 * button, restoring from a backup, and the automatic daily database backup.
 */
export function DesktopDataSettings() {
  const { t } = useTranslation()
  const { message, modal } = App.useApp()
  const { can } = useAuth()
  const queryClient = useQueryClient()
  const manage = can('system:manage')
  const info = useQuery({ queryKey: ['desktop-data'], queryFn: getDataInfo })
  const settings = useQuery({ queryKey: ['app-settings'], queryFn: getSettings })
  const [backupOpen, setBackupOpen] = useState(false)
  const [ask, setAsk] = useState<'restore' | 'move' | null>(null)
  const job = useMaintenanceJob(() => {
    void queryClient.invalidateQueries({ queryKey: ['desktop-data'] })
    void queryClient.invalidateQueries({ queryKey: ['system'] })
  })
  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data, values) => {
      queryClient.setQueryData(['app-settings'], data)
      const live = Object.keys(values).every((key) => data.items.find((i) => i.key === key)?.live)
      void message.success(live ? t('已保存，立即生效') : t('已保存。点击“重新启动后台”后生效'))
    },
    onError: (err) => void message.error(errorText(err)),
  })

  const open = (which: 'data' | 'backups') => {
    openFolder(which).catch((err: unknown) => void message.error(errorText(err)))
  }

  const restore = (path: string, kind: BackupFile['kind']) => {
    modal.confirm({
      title: t('从这个备份恢复？'),
      width: 520,
      content: (
        <Flex vertical gap={8}>
          <Typography.Text code>{path}</Typography.Text>
          <Typography.Text>
            {t('现有数据会先整体移到数据文件夹中的 before-restore-时间 文件夹（不会删除），然后恢复备份并逐项核对；核对不通过时自动放回原来的数据。恢复期间无法使用。')}
          </Typography.Text>
          {kind === 'database' && (
            <Typography.Text type="secondary">
              {t('自动备份只包含数据库：原文件、模型和设置保持现状；备份之后删除的文件将无法打开。')}
            </Typography.Text>
          )}
        </Flex>
      ),
      okText: t('恢复'),
      okButtonProps: { danger: true },
      cancelText: t('取消'),
      onOk: () => job.run(() => startRestore(path)),
    })
  }


  /** parent null: back to the app's own place. */
  const move = async (parent: string | null) => {
    try {
      const check = await checkMove(parent)
      modal.confirm({
        title: parent ? t('把数据文件夹移到这里？') : t('把数据文件夹移回默认位置？'),
        width: 560,
        content: (
          <Flex vertical gap={8}>
            <Typography.Text code>{check.target}</Typography.Text>
            <Typography.Text>
              {t('数据共 {{size}}，目标磁盘可用 {{free}}。', { size: formatBytes(check.size), free: formatBytes(check.free) })}
            </Typography.Text>
            <Typography.Text>
              {t('全部数据（数据库、文件、模型、备份、设置）会复制到新位置并逐个核对，之后改用新位置，并删除原位置的数据。复制期间无法使用，请勿关闭程序。')}
            </Typography.Text>
            {parent && (
              <Typography.Text type="secondary">
                {t('新位置在移动硬盘上时，使用 JacarandaParse 前需要先连接它；未连接时，启动后会提示重新连接或改用默认位置。')}
              </Typography.Text>
            )}
          </Flex>
        ),
        okText: t('移动'),
        cancelText: t('取消'),
        onOk: () => job.run(() => startMove(parent)),
      })
    } catch (err) {
      void message.error(errorText(err))
    }
  }


  const d = info.data
  const moved = d !== undefined && d.path !== d.default
  const dataItems = (settings.data?.items ?? []).filter((i) => i.section === 'data')

  const columns: TableProps<BackupFile>['columns'] = [
    { title: t('时间'), dataIndex: 'created', width: 170, render: (v: number) => timeOf(v) },
    {
      title: t('类型'),
      dataIndex: 'kind',
      render: (_: unknown, b: BackupFile) =>
        b.kind === 'full' ? (
          <Space size={4}>
            <Tag color="purple">{t('完整备份')}</Tag>
            {b.models && <Tag>{t('含模型')}</Tag>}
          </Space>
        ) : (
          <Tag>{t('自动备份（仅数据库）')}</Tag>
        ),
    },
    { title: t('大小'), dataIndex: 'size', width: 110, render: (v: number) => formatBytes(v) },
    {
      title: '',
      key: 'actions',
      width: 90,
      render: (_: unknown, b: BackupFile) => (
        <Button size="small" disabled={!manage || job.busy} onClick={() => restore(b.path, b.kind)}>
          {t('恢复')}
        </Button>
      ),
    },
  ]

  return (
    <>
      {info.error && <Alert type="error" showIcon title={errorText(info.error)} />}
      <Card title={t('数据文件夹')} size="small" loading={info.isPending}>
        {d && (
          <Flex vertical gap={12}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label={t('位置')}>
                <Space size={8} wrap>
                  <Typography.Text code copyable>
                    {d.path}
                  </Typography.Text>
                  {moved && <Tag color="blue">{t('已移动')}</Tag>}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label={t('占用')}>{formatBytes(d.size)}</Descriptions.Item>
              <Descriptions.Item label={t('默认位置')}>
                <Space size={8} wrap>
                  <Typography.Text type="secondary">{d.default}</Typography.Text>
                  {!moved && <Tag>{t('正在使用')}</Tag>}
                </Space>
              </Descriptions.Item>
            </Descriptions>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {t('数据库、上传的文件、模型、设置和备份都在这个文件夹里。磁盘空间不足时，可以把它移到其他磁盘。')}
            </Typography.Text>
            <Flex gap={8} wrap>
              <Button icon={<FolderOpenOutlined />} onClick={() => open('data')}>
                {t('打开数据文件夹')}
              </Button>
              <Button disabled={!manage || job.busy} onClick={() => setAsk('move')}>
                {t('更改位置…')}
              </Button>
              <Tooltip title={moved ? undefined : t('数据已在默认位置')}>
                <Button disabled={!moved || !manage || job.busy} onClick={() => void move(null)}>
                  {t('改回默认位置')}
                </Button>
              </Tooltip>
            </Flex>
          </Flex>
        )}
      </Card>
      <Card
        title={t('备份和恢复')}
        size="small"
        loading={info.isPending}
        extra={
          <Flex gap={8}>
            <Button icon={<FolderOpenOutlined />} size="small" onClick={() => open('backups')}>
              {t('打开备份文件夹')}
            </Button>
          </Flex>
        }
      >
        <Flex vertical gap={12}>
          <Flex gap={8} wrap>
            <Button type="primary" disabled={!manage || job.busy} onClick={() => setBackupOpen(true)}>
              {t('立即备份')}
            </Button>
            <Button disabled={!manage || job.busy} onClick={() => setAsk('restore')}>
              {t('从其他备份文件恢复…')}
            </Button>
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('下面列出数据文件夹中的备份。保存到其他位置的备份不在列表中，可用“从其他备份文件恢复”选择。备份包含业务数据，请勿上传到公开位置。')}
          </Typography.Text>
          <Table
            rowKey="path"
            size="small"
            columns={columns}
            dataSource={d?.backups ?? []}
            pagination={{ pageSize: 10, hideOnSinglePage: true }}
            locale={{ emptyText: t('还没有备份') }}
          />
        </Flex>
      </Card>
      {dataItems.length > 0 && (
        <Card title={t('自动备份')} size="small" loading={settings.isPending}>
          {dataItems.map((item) => (
            <SettingRow
              key={`${item.key}:${String(item.value)}`}
              item={item}
              disabled={!settings.data?.can_change || save.isPending}
              onChange={(value) => save.mutate({ [item.key]: value })}
            />
          ))}
          <Typography.Text type="secondary" style={{ display: 'block', paddingTop: 12, fontSize: 13 }}>
            {d?.last_auto_backup
              ? t('上次自动备份：{{time}}', { time: timeOf(d.last_auto_backup) })
              : t('还没有自动备份。启动后约 1 分钟进行第一次，之后每天一次。')}
          </Typography.Text>
        </Card>
      )}
      <BackupDialog
        open={backupOpen}
        onCancel={() => setBackupOpen(false)}
        onStart={(destination, models) => {
          setBackupOpen(false)
          void job.run(() => startBackup(destination, models))
        }}
      />
      <PathDialog
        open={ask === 'restore'}
        title={t('从其他备份文件恢复')}
        intro={t('选择 JacarandaParse 的备份文件：完整备份（jacara-时间.tar）或自动备份（jacara-时间.db）。')}
        choose={chooseBackupFile}
        placeholder={t('备份文件的完整路径')}
        okText={t('下一步')}
        onCancel={() => setAsk(null)}
        onOk={(path) => {
          setAsk(null)
          restore(path, path.toLowerCase().endsWith('.db') ? 'database' : 'full')
        }}
      />
      <PathDialog
        open={ask === 'move'}
        title={t('更改数据文件夹的位置')}
        intro={t('选择新位置所在的文件夹，例如另一块磁盘或移动硬盘上的某个文件夹，数据会放进其中新建的 JacarandaParse 文件夹。不要选择网络位置（共享文件夹、NAS）或网盘同步文件夹：数据库放在这些位置可能损坏。')}
        choose={() => chooseFolder('move')}
        placeholder={t('选择或输入文件夹的完整路径')}
        okText={t('下一步')}
        onCancel={() => setAsk(null)}
        onOk={(parent) => {
          setAsk(null)
          void move(parent)
        }}
      />
      {job.dialog}
    </>
  )
}
