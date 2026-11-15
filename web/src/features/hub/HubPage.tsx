import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  ExperimentOutlined,
  InboxOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Select,
  Space,
  Table,
  type TableProps,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../../api/client'
import {
  cancelModelUpload,
  createEval,
  type Dataset,
  type DatasetKind,
  type DatasetSplit,
  deleteDataset,
  deleteModelVersion,
  downloadDataset,
  downloadEvalLines,
  downloadModelVersion,
  type EvalRun,
  finishModelUpload,
  type HubModel,
  importDataset,
  KIND_SUFFIX,
  listDatasets,
  listEvals,
  listModels,
  type ModelKind,
  type ModelRef,
  type ModelVersion,
  SOURCE_NAMES,
  SPLIT_NAMES,
  startModelUpload,
  updateModelCard,
  uploadModelFile,
} from '../../api/hub'
import { useAuth } from '../../auth/useAuth'
import { saveFile } from '../../utils/download'
import { formatBytes, formatTime } from '../../utils/format'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }
const SPLITS: DatasetSplit[] = ['train', 'val', 'test', 'all']
const DATASET_KINDS: Record<DatasetKind, string> = { get ocr_rec() { return t('识别（单行图像）') }, get ocr_det() { return t('检测（整页图像及文本框）') } }
const EVIDENCE_COLOR = { process: 'orange', thin: 'gold', trend: 'green' } as const

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function pct(x: number | undefined): string {
  return x === undefined ? '—' : `${(x * 100).toFixed(1)}%`
}

// ---- Model library ----

interface ImportTarget {
  name?: string
  kind?: ModelKind
}

/**
 * Import a model: upload weights (and a recognition dictionary), or a zip exported from the library. Files go
 * directly into models/hub and become a new version once validated.
 */
function ImportModelDialog({
  target,
  onClose,
  models,
  kinds,
  datasetNames,
}: {
  target: ImportTarget | null
  onClose: () => void
  models: HubModel[]
  kinds: Record<ModelKind, string>
  datasetNames: string[]
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'files' | 'archive'>('files')
  const [name, setName] = useState(target?.name ?? '')
  const [kind, setKind] = useState<ModelKind>(target?.kind ?? 'ocr_rec')
  const [weights, setWeights] = useState<File | null>(null)
  const [dictFile, setDictFile] = useState<File | null>(null)
  const [archive, setArchive] = useState<File | null>(null)
  const [baseModel, setBaseModel] = useState('')
  const [license, setLicense] = useState('')
  const [datasets, setDatasets] = useState<string[]>([])
  const [description, setDescription] = useState('')
  const [progress, setProgress] = useState<Record<string, number>>({})
  const existing = models.find((m) => m.name === name.trim())
  const lockedKind = existing?.kind
  const effectiveKind = lockedKind ?? kind

  const submit = useMutation({
    mutationFn: async () => {
      const { upload_id: uploadId } = await startModelUpload()
      try {
        const files = mode === 'archive' ? [archive!] : [weights!, ...(dictFile ? [dictFile] : [])]
        for (const file of files) {
          await uploadModelFile(uploadId, file, (p) => setProgress((old) => ({ ...old, [file.name]: p })))
        }
        return await finishModelUpload(uploadId, {
          name: name.trim(),
          ...(mode === 'archive'
            ? { archive: archive!.name }
            : { kind: effectiveKind, weights: weights!.name, dict_file: dictFile?.name }),
          base_model: baseModel.trim() || undefined,
          license: license.trim() || undefined,
          datasets,
          description,
        })
      } catch (err) {
        await cancelModelUpload(uploadId).catch(() => undefined)
        throw err
      }
    },
    onSuccess: (v) => {
      void message.success(t('已导入 {{trim}} v{{version}}', { trim: name.trim(), version: v.version }))
      void queryClient.invalidateQueries({ queryKey: ['hub-models'] })
      void queryClient.invalidateQueries({ queryKey: ['models'] })
      onClose()
    },
    onError: (err) => {
      setProgress({})
      void message.error(errorText(err))
    },
  })

  const ready = name.trim() !== '' && (mode === 'archive' ? archive !== null : weights !== null)
  const suffix = KIND_SUFFIX[effectiveKind]
  const picker = (accept: string, file: File | null, set: (f: File | null) => void, hint: string) => (
    <Upload.Dragger
      accept={accept}
      multiple={false}
      showUploadList={false}
      beforeUpload={(f) => {
        set(f)
        return false
      }}
      disabled={submit.isPending}
    >
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      {file ? (
        <Typography.Text strong>
          {t('{{name}}（{{formatBytes}}）', { name: file.name, formatBytes: formatBytes(file.size) })}
        </Typography.Text>
      ) : (
        <Typography.Text>{hint}</Typography.Text>
      )}
      {file && progress[file.name] !== undefined && (
        <Progress percent={progress[file.name]} size="small" style={{ padding: '0 24px' }} />
      )}
    </Upload.Dragger>
  )

  return (
    <Modal
      title={target?.name ? t('{{name}}：导入新版本', { name: target.name }) : t('导入模型')}
      open={target !== null}
      onCancel={submit.isPending ? undefined : onClose}
      width={640}
      maskClosable={false}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={submit.isPending}>
          {t('取消')}
        </Button>,
        <Button
          key="ok"
          type="primary"
          icon={<CloudUploadOutlined />}
          disabled={!ready}
          loading={submit.isPending}
          onClick={() => submit.mutate()}
        >
          {t('上传并检查')}
        </Button>,
      ]}
    >
      <Flex vertical gap={12}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={mode}
          onChange={(e) => setMode(e.target.value as 'files' | 'archive')}
          options={[
            { value: 'files', label: t('权重文件') },
            { value: 'archive', label: t('导出的模型 zip') },
          ]}
        />
        <Flex gap={8} wrap>
          <AutoComplete
            style={{ flex: 1, minWidth: 220 }}
            value={name}
            onChange={setName}
            options={models.map((m) => ({ value: m.name, label: t('{{name}}（{{kind_label}}）', { name: m.name, kind_label: m.kind_label }) }))}
            placeholder={t('模型名称：字母、数字及 ._-，例如 receipt-rec')}
            disabled={Boolean(target?.name)}
          />
          {mode === 'files' && (
            <Select
              style={{ width: 170 }}
              value={effectiveKind}
              onChange={setKind}
              disabled={lockedKind !== undefined}
              options={(Object.keys(kinds) as ModelKind[]).map((k) => ({ value: k, label: kinds[k] }))}
            />
          )}
        </Flex>
        {lockedKind && (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t('{{trim}} 已有版本，本次导入将作为其下一个版本，并沿用其种类（{{value}}）。', { trim: name.trim(), value: kinds[lockedKind] })}
          </Typography.Text>
        )}
        {mode === 'files' ? (
          <>
            {picker(suffix, weights, setWeights, t('拖入权重文件（{{suffix}}），或点击选择', { suffix }))}
            {effectiveKind === 'ocr_rec' &&
              picker('.txt', dictFile, setDictFile, t('识别字典（.txt，每行一个字符）：模型已内置字符表时无需提供'))}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('支持 ONNX 和 GGUF 格式。PaddleOCR 训练得到的 Paddle 推理模型需先使用官方工具')}
              <Typography.Text code>paddlex --paddle2onnx</Typography.Text>{t('转换为 ONNX。')}
            </Typography.Text>
          </>
        ) : (
          picker('.zip', archive, setArchive, t('拖入从本程序导出的模型 zip 文件（包含模型卡 README.md）'))
        )}
        <Flex gap={8} wrap>
          <Input
            style={{ flex: 1, minWidth: 200 }}
            value={baseModel}
            onChange={(e) => setBaseModel(e.target.value)}
            placeholder={t('基础模型（选填），例如 PP-OCRv6_rec_small')}
          />
          <Input
            style={{ width: 160 }}
            value={license}
            onChange={(e) => setLicense(e.target.value)}
            placeholder={t('许可证，例如 apache-2.0')}
          />
        </Flex>
        <Select
          mode="tags"
          value={datasets}
          onChange={setDatasets}
          placeholder={t('训练数据集（选填）')}
          options={datasetNames.map((d) => ({ value: d, label: d }))}
        />
        <Input.TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t('说明：训练方法、改动内容及注意事项（写入模型卡）')}
          autoSize={{ minRows: 2, maxRows: 6 }}
          maxLength={5000}
        />
      </Flex>
    </Modal>
  )
}

function CardDialog({
  target,
  onClose,
  datasetNames,
}: {
  target: { model: HubModel; version: ModelVersion } | null
  onClose: () => void
  datasetNames: string[]
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const v = target?.version
  const [baseModel, setBaseModel] = useState(v?.base_model ?? '')
  const [license, setLicense] = useState(v?.license ?? '')
  const [datasets, setDatasets] = useState<string[]>(v?.datasets ?? [])
  const [description, setDescription] = useState(v?.description ?? '')
  const save = useMutation({
    mutationFn: () =>
      updateModelCard(target!.model.name, target!.version.version, {
        base_model: baseModel,
        license,
        datasets,
        description,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['hub-models'] })
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <Modal
      title={target ? t('模型卡：{{name}} v{{version}}', { name: target.model.name, version: target.version.version }) : ''}
      open={target !== null}
      onCancel={onClose}
      onOk={() => save.mutate()}
      okText={t('保存')}
      confirmLoading={save.isPending}
    >
      <Flex vertical gap={10}>
        <Input value={baseModel} onChange={(e) => setBaseModel(e.target.value)} placeholder={t('基础模型（微调起点）')} />
        <Input value={license} onChange={(e) => setLicense(e.target.value)} placeholder={t('许可证，例如 apache-2.0')} />
        <Select
          mode="tags"
          value={datasets}
          onChange={setDatasets}
          placeholder={t('训练数据集')}
          options={datasetNames.map((d) => ({ value: d, label: d }))}
        />
        <Input.TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          autoSize={{ minRows: 3, maxRows: 10 }}
          maxLength={5000}
        />
      </Flex>
    </Modal>
  )
}

function checksText(checks: Record<string, unknown>): string {
  const parts: string[] = []
  if (checks.format === 'gguf') parts.push(`GGUF v${String(checks.gguf_version)}`)
  if (checks.format === 'onnx') parts.push(t('ONNX，可由 onnxruntime 加载'))
  if (typeof checks.characters === 'number') {
    const from = checks.dict_source === 'model' ? t('模型内置') : t('字典文件')
    parts.push(t('字符表 {{characters}} 个字符（{{from}}）', { characters: checks.characters, from }))
  }
  if (typeof checks.classes === 'number') parts.push(t('输出 {{classes}} 类', { classes: checks.classes }))
  if (typeof checks.dict_note === 'string') parts.push(checks.dict_note)
  return parts.join(t('；'))
}

function ModelsTab({ canManage, datasetNames }: { canManage: boolean; datasetNames: string[] }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const models = useQuery({ queryKey: ['hub-models'], queryFn: listModels })
  const [importing, setImporting] = useState<ImportTarget | null>(null)
  const [editing, setEditing] = useState<{ model: HubModel; version: ModelVersion } | null>(null)
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['hub-models'] })
    void queryClient.invalidateQueries({ queryKey: ['models'] })
  }
  const remove = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) => deleteModelVersion(name, version),
    onSuccess: refresh,
    onError: (err) => void message.error(errorText(err)),
  })
  const download = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) => downloadModelVersion(name, version),
    onSuccess: (file) => void saveFile(file),
    onError: (err) => void message.error(errorText(err)),
  })
  const data = models.data

  const columns = (model: HubModel): TableProps<ModelVersion>['columns'] => [
    {
      title: t('版本'),
      key: 'version',
      width: 110,
      render: (_, v) => (
        <Space size={4} wrap>
          <Typography.Text strong>v{v.version}</Typography.Text>
          {v.in_use && (
            <Tag color="green" icon={<CheckCircleOutlined />}>
              {t('使用中')}
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: t('文件'),
      key: 'files',
      render: (_, v) => (
        <Flex vertical gap={2}>
          {v.files.map((f) => (
            <Typography.Text key={f.name} style={{ fontSize: 13 }}>
              {f.role === 'dict' ? t('字典 ') : ''}
              {t('{{name}}（{{formatBytes}}）', { name: f.name, formatBytes: formatBytes(f.size) })}{' '}
              <Tooltip title={t('SHA-256：{{sha256}}', { sha256: f.sha256 })}>
                <Typography.Text type="secondary" style={{ ...MONO, fontSize: 12 }} copyable={{ text: f.sha256 }}>
                  {f.sha256.slice(0, 12)}
                </Typography.Text>
              </Tooltip>
            </Typography.Text>
          ))}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {checksText(v.checks)}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: t('模型卡'),
      key: 'card',
      render: (_, v) => (
        <Flex vertical gap={2} style={{ maxWidth: 360 }}>
          {v.base_model && <Typography.Text style={{ fontSize: 13 }}>{t('基础模型：{{base_model}}', { base_model: v.base_model })}</Typography.Text>}
          {v.license && <Typography.Text style={{ fontSize: 13 }}>{t('许可：{{license}}', { license: v.license })}</Typography.Text>}
          {v.datasets.length > 0 && (
            <Space size={4} wrap>
              {v.datasets.map((d) => (
                <Tag key={d}>{d}</Tag>
              ))}
            </Space>
          )}
          {v.description && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 13, margin: 0 }} ellipsis={{ rows: 3, expandable: true }}>
              {v.description}
            </Typography.Paragraph>
          )}
        </Flex>
      ),
    },
    {
      title: t('导入'),
      key: 'imported',
      width: 150,
      render: (_, v) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {v.imported_at ? formatTime(v.imported_at) : ''}
          <br />
          {v.imported_by ?? ''}
        </Typography.Text>
      ),
    },
    ...(canManage
      ? [
          {
            title: '',
            key: 'actions',
            width: 130,
            render: (_: unknown, v: ModelVersion) => (
              <Space size={4} wrap>
                <Tooltip title={t('导出为 zip（权重及模型卡），用于在其他计算机上导入')}>
                  <Button size="small" icon={<DownloadOutlined />} onClick={() => download.mutate({ name: model.name, version: v.version })} />
                </Tooltip>
                <Tooltip title={t('编辑模型卡')}>
                  <Button size="small" icon={<EditOutlined />} onClick={() => setEditing({ model, version: v })} />
                </Tooltip>
                <Tooltip title={v.in_use ? t('正在使用，不能删除：先在「设置 → 模型」中换成其他版本') : undefined}>
                  <Popconfirm
                    title={t('删除 {{name}} v{{version}}？', { name: model.name, version: v.version })}
                    description={t('文件将从 models/hub 中删除，且无法恢复。')}
                    okText={t('删除')}
                    okButtonProps={{ danger: true }}
                    disabled={v.in_use}
                    onConfirm={() => remove.mutate({ name: model.name, version: v.version })}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} disabled={v.in_use} />
                  </Popconfirm>
                </Tooltip>
              </Space>
            ),
          },
        ]
      : []),
  ]

  return (
    <Flex vertical gap={12}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Typography.Text type="secondary">
          {t('模型文件存放在本机 models/hub/ 目录，每个版本对应一个子目录和一张模型卡。启用版本请在')}{' '}
          <Link to="/settings/models">{t('设置 → 模型')}</Link>{' '}{t('中选择（已导入的全部版本均列在下拉列表中）。')}
        </Typography.Text>
        {canManage && (
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setImporting({})}>
            {t('导入模型')}
          </Button>
        )}
      </Flex>
      {data && data.env_locked.length > 0 && (
        <Alert
          type="info"
          showIcon
          title={t('.env 中已指定：{{kinds}}。这些模型无法在网页中更换。', {
            kinds: data.env_locked.map((k) => data.kinds[k as ModelKind] ?? k).join(t('、')),
          })}
        />
      )}
      {models.isError && <Alert type="error" showIcon title={t('无法读取模型列表')} description={errorText(models.error)} />}
      {data && data.models.length === 0 && (
        <Empty description={t('还没有导入模型。支持导入 OCR 文字检测、文字识别和表格结构模型（ONNX），以及大模型和视觉模型（GGUF）。')} />
      )}
      {data?.models.map((model) => (
        <Card
          key={model.name}
          size="small"
          title={
            <Space>
              <Typography.Text strong>{model.name}</Typography.Text>
              <Tag color="blue">{model.kind_label}</Tag>
            </Space>
          }
          extra={
            canManage && (
              <Button size="small" icon={<CloudUploadOutlined />} onClick={() => setImporting({ name: model.name, kind: model.kind })}>
                {t('导入新版本')}
              </Button>
            )
          }
        >
          <Table rowKey="version" size="small" pagination={false} columns={columns(model)} dataSource={model.versions} scroll={{ x: 900 }} />
        </Card>
      ))}
      {importing && (
        <ImportModelDialog
          target={importing}
          onClose={() => setImporting(null)}
          models={data?.models ?? []}
          kinds={data?.kinds ?? ({} as Record<ModelKind, string>)}
          datasetNames={datasetNames}
        />
      )}
      {editing && <CardDialog target={editing} onClose={() => setEditing(null)} datasetNames={datasetNames} />}
    </Flex>
  )
}

// ---- Dataset library ----

function ImportDatasetDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [kind, setKind] = useState<DatasetKind>('ocr_rec')
  const [description, setDescription] = useState('')
  const submit = useMutation({
    mutationFn: () => importDataset(file!, name.trim(), kind, description),
    onSuccess: (d) => {
      const skipped = Object.values(d.problems).reduce((a, b) => a + b, 0)
      void message.success(
        skipped
          ? t('已保存为数据集：{{name}} v{{version}}，跳过 {{skipped}} 行', { name: d.name, version: d.version, skipped })
          : t('已保存为数据集：{{name}} v{{version}}', { name: d.name, version: d.version }),
      )
      void queryClient.invalidateQueries({ queryKey: ['hub-datasets'] })
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <Modal
      title={t('上传数据集')}
      open={open}
      onCancel={onClose}
      width={600}
      footer={[
        <Button key="cancel" onClick={onClose}>
          {t('取消')}
        </Button>,
        <Button
          key="ok"
          type="primary"
          disabled={!file || !name.trim()}
          loading={submit.isPending}
          onClick={() => submit.mutate()}
        >
          {t('上传并检查')}
        </Button>,
      ]}
    >
      <Flex vertical gap={12}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={kind}
          onChange={(e) => setKind(e.target.value as DatasetKind)}
          options={(Object.keys(DATASET_KINDS) as DatasetKind[]).map((k) => ({ value: k, label: DATASET_KINDS[k] }))}
        />
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('名称，例如“收据识别”；再次上传同名数据集时版本号加 1')} maxLength={64} />
        <Upload.Dragger
          accept=".zip"
          multiple={false}
          showUploadList={false}
          beforeUpload={(f) => {
            setFile(f)
            return false
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          {file ? (
            <Typography.Text strong>
              {t('{{name}}（{{formatBytes}}）', { name: file.name, formatBytes: formatBytes(file.size) })}
            </Typography.Text>
          ) : (
            <Typography.Text>{t('拖入 zip 文件（最大 1 GB）')}</Typography.Text>
          )}
        </Upload.Dragger>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {kind === 'ocr_rec'
            ? t('支持 PaddleOCR / PaddleX 识别标注格式：rec_gt_train.txt、rec_gt_val.txt、rec_gt_test.txt、train.txt、val.txt，或 PPOCRLabel 导出的 rec_gt.txt。每行格式：图片路径、Tab、文字。')
            : t('支持 PaddleOCR / PaddleX 检测标注格式：det_gt_train.txt、det_gt_val.txt、det_gt_test.txt、train.txt、val.txt，或 PPOCRLabel 的 Label.txt。每行格式：图片路径、Tab、文本框 JSON。')}
        </Typography.Text>
        <Input.TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t('数据集卡：数据来源、标注方法及注意事项')}
          autoSize={{ minRows: 2, maxRows: 6 }}
          maxLength={5000}
        />
      </Flex>
    </Modal>
  )
}

function DatasetsTab({
  datasets,
  loading,
  canChange,
  onEvaluate,
}: {
  datasets: Dataset[]
  loading: boolean
  canChange: boolean
  onEvaluate: (d: Dataset) => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [importing, setImporting] = useState(false)
  const download = useMutation({
    mutationFn: downloadDataset,
    onSuccess: (file) => void saveFile(file),
    onError: (err) => void message.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: deleteDataset,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['hub-datasets'] })
      void queryClient.invalidateQueries({ queryKey: ['hub-evals'] })
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const columns: TableProps<Dataset>['columns'] = [
    {
      title: t('数据集'),
      key: 'name',
      render: (_, d) => (
        <Flex vertical gap={2}>
          <Typography.Text strong>
            {d.name} v{d.version}
          </Typography.Text>
          {d.description && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: 0, maxWidth: 320 }} ellipsis={{ rows: 2, expandable: true }}>
              {d.description}
            </Typography.Paragraph>
          )}
        </Flex>
      ),
    },
    {
      title: t('种类'),
      key: 'kind',
      width: 150,
      render: (_, d) => (
        <Space size={4} wrap>
          <Tag color={d.kind === 'ocr_rec' ? 'blue' : 'purple'}>{d.kind === 'ocr_rec' ? t('识别') : t('检测')}</Tag>
          <Tag>{SOURCE_NAMES[d.source]}</Tag>
        </Space>
      ),
    },
    {
      title: t('划分'),
      key: 'counts',
      render: (_, d) => (
        <Flex vertical gap={0}>
          {SPLITS.filter((s) => d.counts[s]).map((s) => (
            <Typography.Text key={s} style={{ fontSize: 13 }}>
              {SPLIT_NAMES[s]} {d.counts[s]} {d.kind === 'ocr_rec' ? t('行') : t('页')}
              {d.groups[s] ? t('、{{value}} 组', { value: d.groups[s] }) : ''}
            </Typography.Text>
          ))}
          {Object.keys(d.problems).length > 0 && (
            <Tooltip title={Object.entries(d.problems).map(([r, n]) => t('{{r}}：{{n}}', { r, n })).join(t('；'))}>
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                {t('跳过 {{reduce}} 行', { reduce: Object.values(d.problems).reduce((a, b) => a + b, 0) })}
              </Typography.Text>
            </Tooltip>
          )}
        </Flex>
      ),
    },
    {
      title: t('大小'),
      key: 'size',
      width: 100,
      render: (_, d) => (
        <Tooltip title={t('SHA-256：{{sha256}}', { sha256: d.sha256 })}>
          <span>{formatBytes(d.size)}</span>
        </Tooltip>
      ),
    },
    { title: t('时间'), key: 'created', width: 150, render: (_, d) => formatTime(d.created_at) },
    {
      title: '',
      key: 'actions',
      width: 200,
      render: (_, d) => (
        <Space size={4} wrap>
          {canChange && (
            <Button size="small" icon={<ExperimentOutlined />} onClick={() => onEvaluate(d)}>
              {t('评测')}
            </Button>
          )}
          <Button size="small" icon={<DownloadOutlined />} loading={download.isPending && download.variables?.id === d.id} onClick={() => download.mutate(d)} />
          {canChange && (
            <Popconfirm
              title={t('删除 {{name}} v{{version}}？', { name: d.name, version: d.version })}
              description={t('基于此数据集的评测记录将一并删除。')}
              okText={t('删除')}
              okButtonProps={{ danger: true }}
              onConfirm={() => remove.mutate(d.id)}
            >
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Typography.Text type="secondary">
          {t('数据集由业务单据的图像及其标注组成，存储在本机对象存储中，并包含在系统备份内。「OCR 样本」中识别样本和检测样本的导出可直接保存为数据集。')}
        </Typography.Text>
        {canChange && (
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setImporting(true)}>
            {t('上传数据集')}
          </Button>
        )}
      </Flex>
      <Table
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={datasets}
        scroll={{ x: 900 }}
        pagination={false}
        locale={{ emptyText: <Empty description={t('暂无数据集')} /> }}
      />
      {importing && <ImportDatasetDialog open onClose={() => setImporting(false)} />}
    </Flex>
  )
}

// ---- Evaluation ----

function refValue(ref: ModelRef): string {
  return ref.source === 'hub' ? `hub:${ref.name}:${ref.version}` : ref.source
}

function parseRef(value: string): ModelRef {
  if (value === 'current' || value === 'builtin') return { source: value }
  const [, name, version] = value.split(':')
  return { source: 'hub', name, version: Number(version) }
}

function EvalDialog({
  dataset,
  datasets,
  models,
  onClose,
  onStarted,
}: {
  dataset: Dataset | null
  datasets: Dataset[]
  models: HubModel[]
  onClose: () => void
  onStarted: () => void
}) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState(dataset?.id ?? datasets[0]?.id)
  const chosen = datasets.find((d) => d.id === datasetId)
  const splits = SPLITS.filter((s) => chosen?.counts[s])
  const [split, setSplit] = useState<DatasetSplit | undefined>()
  const effectiveSplit = split && splits.includes(split) ? split : splits.includes('val') ? 'val' : splits[0]
  const slot: ModelKind = chosen?.kind === 'ocr_det' ? 'ocr_det' : 'ocr_rec'
  const options = [
    { value: 'current', label: t('当前使用') },
    { value: 'builtin', label: t('默认模型') },
    ...models
      .filter((m) => m.kind === slot)
      .flatMap((m) =>
        m.versions.map((v) => ({
          value: refValue({ source: 'hub', name: m.name, version: v.version }),
          label: `${m.name} v${v.version}` + (v.in_use ? t('（使用中）') : ''),
        })),
      ),
  ]
  const [a, setA] = useState('current')
  const [b, setB] = useState<string | undefined>()
  const submit = useMutation({
    mutationFn: () => createEval({ dataset_id: datasetId!, split: effectiveSplit!, a: parseRef(a), b: parseRef(b!) }),
    onSuccess: () => {
      void message.success(t('已加入后台队列，完成后结果将显示在下方'))
      void queryClient.invalidateQueries({ queryKey: ['hub-evals'] })
      onClose()
      onStarted()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <Modal
      title={t('对比两个 OCR 模型')}
      open
      onCancel={onClose}
      onOk={() => submit.mutate()}
      okText={t('开始评测')}
      okButtonProps={{ disabled: !datasetId || !effectiveSplit || !b || a === b }}
      confirmLoading={submit.isPending}
      width={560}
    >
      <Flex vertical gap={12}>
        <Select
          value={datasetId}
          onChange={setDatasetId}
          placeholder={t('数据集')}
          options={datasets.map((d) => ({
            value: d.id,
            label: `${d.name} v${d.version}` + t('（{{kind}}）', { kind: d.kind === 'ocr_rec' ? t('识别') : t('检测') }),
          }))}
        />
        <Radio.Group
          value={effectiveSplit}
          onChange={(e) => setSplit(e.target.value as DatasetSplit)}
          options={splits.map((s) => ({ value: s, label: `${SPLIT_NAMES[s]} ${chosen?.counts[s]}` }))}
        />
        <Flex gap={8} align="center">
          <Typography.Text style={{ width: 24 }}>A</Typography.Text>
          <Select style={{ flex: 1 }} value={a} onChange={setA} options={options} />
        </Flex>
        <Flex gap={8} align="center">
          <Typography.Text style={{ width: 24 }}>B</Typography.Text>
          <Select style={{ flex: 1 }} value={b} onChange={setB} options={options} placeholder={t('待评测模型')} />
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {slot === 'ocr_rec'
            ? t('识别：对每张单行图像仅执行识别，统计整行完全一致的比例（Exact Match）及 1 − 平均归一化编辑距离（Char Match），指标定义参照 RapidOCR 作者的 TextRecMetric。')
            : t('检测：对每张整页图像仅执行检测，IoU > 0.5 视为匹配，按 PaddleOCR 的 DetectionIoUEvaluator 计算 Precision、Recall 和 H-mean。')}
          {t('反复依据验证集选择模型，会使结果逐渐过拟合验证集；正式验收请使用锁定测试集。')}
        </Typography.Text>
      </Flex>
    </Modal>
  )
}

function EvalDetail({ run }: { run: EvalRun }) {
  const { t } = useTranslation()
  const s = run.summary
  if (!s) {
    return run.error ? <Alert type="error" showIcon title={t('评测失败')} description={run.error} /> : null
  }
  const [a, b] = run.arms
  const rows =
    s.task === 'rec'
      ? [
          [t('Exact Match（整行完全一致）'), pct(s.a.exact_match), pct(s.b.exact_match)],
          [t('Char Match（1 − 平均归一化编辑距离）'), pct(s.a.char_match), pct(s.b.char_match)],
          [t('仅空格、大小写或全角/半角不同'), s.a.format_only, s.b.format_only],
          [t('未识别出文字'), s.a.empty, s.b.empty],
        ]
      : [
          ['Precision', pct(s.a.precision), pct(s.b.precision)],
          ['Recall', pct(s.a.recall), pct(s.b.recall)],
          ['H-mean', pct(s.a.hmean), pct(s.b.hmean)],
          [t('检测框数 / 匹配数'), `${s.a.det_boxes} / ${s.a.matched}`, `${s.b.det_boxes} / ${s.b.matched}`],
        ]
  return (
    <Flex vertical gap={12} style={{ padding: '4px 8px' }}>
      <Table
        size="small"
        pagination={false}
        rowKey={(r) => String(r[0])}
        dataSource={rows}
        columns={[
          { title: '', key: 'k', render: (_, r) => r[0] },
          { title: t('A：{{label}}', { label: a?.label }), key: 'a', align: 'right', render: (_, r) => r[1] },
          { title: t('B：{{label}}', { label: b?.label }), key: 'b', align: 'right', render: (_, r) => r[2] },
        ]}
      />
      <Descriptions
        size="small"
        column={1}
        items={[
          {
            key: 'changed',
            label: s.task === 'rec' ? t('B 修正 / 引入错误') : t('B 新增匹配 / 丢失匹配'),
            children: t('{{fixed}} / {{broken}}（精确 McNemar p = {{mcnemar_p}}）', { fixed: s.fixed, broken: s.broken, mcnemar_p: s.mcnemar_p }),
          },
          ...(s.task === 'rec'
            ? [
                {
                  key: 'ci',
                  label: t('Exact Match 差值（B − A）95% 置信区间'),
                  children: s.bootstrap_ci
                    ? `${pct(s.bootstrap_ci[0])} ～ ${pct(s.bootstrap_ci[1])}` + // i18n-ignore: range separator
                      (s.grouped ? t('（按业务单据分组 bootstrap）') : t('（按业务单据分组 bootstrap；无分组信息时每张图像单独成组，区间偏窄）'))
                    : t('不足 2 组，不计算'),
                },
              ]
            : []),
          { key: 'evidence', label: t('证据'), children: s.evidence_text },
          { key: 'time', label: t('用时'), children: t('{{seconds}} 秒', { seconds: s.seconds }) },
          ...run.arms.map((arm) => ({
            key: arm.name,
            label: t('{{name}} 的文件', { name: arm.name }),
            children: (
              <Typography.Text style={{ ...MONO, fontSize: 12 }}>
                {(['det', 'rec', 'dict'] as const)
                  .filter((k) => arm[k])
                  .map((k) => t('{{value}}（{{slice}}）', { value: arm[k], slice: (arm.sha256[k] ?? '').slice(0, 12) }))
                  .join(t('；')) || t('默认模型')}
              </Typography.Text>
            ),
          })),
        ]}
      />
    </Flex>
  )
}

function EvalsTab({ canChange, onNew }: { canChange: boolean; onNew: () => void }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const runs = useQuery({
    queryKey: ['hub-evals'],
    queryFn: listEvals,
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'queued' || r.status === 'running') ? 3000 : false),
  })
  const download = useMutation({
    mutationFn: downloadEvalLines,
    onSuccess: (file) => void saveFile(file),
    onError: (err) => void message.error(errorText(err)),
  })
  const statusTag = (r: EvalRun) =>
    ({
      queued: <Tag>{t('排队中')}</Tag>,
      running: <Tag color="processing">{t('运行中 {{progress}}', { progress: r.progress })}</Tag>,
      done: <Tag color="success">{t('完成')}</Tag>,
      failed: <Tag color="error">{t('失败')}</Tag>,
    })[r.status]
  const columns: TableProps<EvalRun>['columns'] = [
    { title: t('时间'), key: 'time', width: 150, render: (_, r) => formatTime(r.created_at) },
    {
      title: t('数据集'),
      key: 'dataset',
      render: (_, r) => (
        <>
          {r.dataset} · {SPLIT_NAMES[r.split as DatasetSplit] ?? r.split}{' '}
          <Tag color={r.task === 'rec' ? 'blue' : 'purple'}>{r.task === 'rec' ? t('识别') : t('检测')}</Tag>
        </>
      ),
    },
    {
      title: 'A → B',
      key: 'arms',
      render: (_, r) => `${r.arms[0]?.label} → ${r.arms[1]?.label}`,
    },
    {
      title: t('结果'),
      key: 'result',
      render: (_, r) => {
        const s = r.summary
        if (!s) return statusTag(r)
        const [x, y] = s.task === 'rec' ? [s.a.exact_match, s.b.exact_match] : [s.a.hmean, s.b.hmean]
        return (
          <Space size={4} wrap>
            <Typography.Text>
              {s.task === 'rec' ? 'Exact Match' : 'H-mean'} {pct(x)} → <Typography.Text strong>{pct(y)}</Typography.Text>
            </Typography.Text>
            <Tag color={EVIDENCE_COLOR[s.evidence]}>
              {{ process: t('仅可验证流程'), thin: t('证据不足'), trend: t('可参考趋势') }[s.evidence]}
            </Tag>
          </Space>
        )
      },
    },
    {
      title: '',
      key: 'actions',
      width: 60,
      render: (_, r) =>
        r.status === 'done' && (
          <Tooltip title={t('下载逐条结果（jsonl，包含单据文字，仅保存在本机）')}>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download.mutate(r.id)} />
          </Tooltip>
        ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Typography.Text type="secondary">
          {t('在数据集的指定划分（train / val / test）上对比两个模型的识别结果。评测为只读操作，不会更改正在使用的模型；确认 B 显著优于 A 且样本量充足后，再到「设置 → 模型」中启用。')}
        </Typography.Text>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void runs.refetch()} />
          {canChange && (
            <Button type="primary" icon={<ExperimentOutlined />} onClick={onNew}>
              {t('新建评测')}
            </Button>
          )}
        </Space>
      </Flex>
      <Table
        rowKey="id"
        size="middle"
        loading={runs.isLoading}
        columns={columns}
        dataSource={runs.data ?? []}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        expandable={{
          expandedRowRender: (r) => <EvalDetail run={r} />,
          rowExpandable: (r) => r.status === 'done' || r.status === 'failed',
        }}
        locale={{ emptyText: <Empty description={t('暂无评测记录')} /> }}
      />
    </Flex>
  )
}

/** Models and datasets: import and export models and datasets, select a version, compare two OCR models. */
export function HubPage() {
  const { t } = useTranslation()
  const { can } = useAuth()
  const [tab, setTab] = useState('models')
  const [evaluating, setEvaluating] = useState<{ dataset: Dataset | null } | null>(null)
  const canExport = can('document:export')
  const datasets = useQuery({ queryKey: ['hub-datasets'], queryFn: listDatasets, enabled: canExport })
  const models = useQuery({ queryKey: ['hub-models'], queryFn: listModels })
  const datasetNames = useMemo(() => [...new Set((datasets.data ?? []).map((d) => d.name))], [datasets.data])
  return (
    <Flex vertical gap={16}>
      <Flex vertical gap={4}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {t('模型和数据集')}
        </Typography.Title>
        <Typography.Text type="secondary">
          {t('管理本地模型与数据集：导入在系统外训练的模型（如 PaddleOCR 导出的 ONNX），在数据集上对比评测，并选择启用的版本。全部操作在本机完成，不访问网络。')}
        </Typography.Text>
      </Flex>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'models',
            label: t('模型'),
            children: <ModelsTab canManage={can('system:manage')} datasetNames={datasetNames} />,
          },
          ...(canExport
            ? [
                {
                  key: 'datasets',
                  label: t('数据集 {{value}}', { value: datasets.data?.length ?? '' }),
                  children: (
                    <DatasetsTab
                      datasets={datasets.data ?? []}
                      loading={datasets.isLoading}
                      canChange={can('value:review')}
                      onEvaluate={(d) => setEvaluating({ dataset: d })}
                    />
                  ),
                },
                {
                  key: 'evals',
                  label: t('评测'),
                  children: (
                    <EvalsTab
                      canChange={can('value:review') && (datasets.data?.length ?? 0) > 0}
                      onNew={() => setEvaluating({ dataset: null })}
                    />
                  ),
                },
              ]
            : []),
        ]}
      />
      {evaluating && (
        <EvalDialog
          dataset={evaluating.dataset}
          datasets={datasets.data ?? []}
          models={models.data?.models ?? []}
          onClose={() => setEvaluating(null)}
          onStarted={() => setTab('evals')}
        />
      )}
    </Flex>
  )
}
