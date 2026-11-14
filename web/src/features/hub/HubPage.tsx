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
import { ApiError } from '../../api/client'
import {
  activateModelVersion,
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

const MONO = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }
const SPLITS: DatasetSplit[] = ['train', 'val', 'test', 'all']
const DATASET_KINDS: Record<DatasetKind, string> = { ocr_rec: '识别（单行小图）', ocr_det: '检测（整页图和框）' }
const EVIDENCE_COLOR = { process: 'orange', thin: 'gold', trend: 'green' } as const

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err)
}

function pct(x: number | undefined): string {
  return x === undefined ? '—' : `${(x * 100).toFixed(1)}%`
}

// ---- 模型库 ----

interface ImportTarget {
  name?: string
  kind?: ModelKind
}

/** 导入模型：传权重（和识别字典），或者模型库导出的 zip。文件直接传进 models/hub，检查通过才成为新版本。 */
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
      void message.success(`已导入 ${name.trim()} v${v.version}`)
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
          {file.name}（{formatBytes(file.size)}）
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
      title={target?.name ? `${target.name}：导入新版本` : '导入模型'}
      open={target !== null}
      onCancel={submit.isPending ? undefined : onClose}
      width={640}
      maskClosable={false}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={submit.isPending}>
          取消
        </Button>,
        <Button
          key="ok"
          type="primary"
          icon={<CloudUploadOutlined />}
          disabled={!ready}
          loading={submit.isPending}
          onClick={() => submit.mutate()}
        >
          上传并检查
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
            { value: 'files', label: '权重文件' },
            { value: 'archive', label: '模型库导出的 zip' },
          ]}
        />
        <Flex gap={8} wrap>
          <AutoComplete
            style={{ flex: 1, minWidth: 220 }}
            value={name}
            onChange={setName}
            options={models.map((m) => ({ value: m.name, label: `${m.name}（${m.kind_label}）` }))}
            placeholder="模型名：字母、数字、._-，例如 receipt-rec"
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
            {name.trim()} 已经有版本了，这次会成为它的下一个版本，种类跟着它（{kinds[lockedKind]}）。
          </Typography.Text>
        )}
        {mode === 'files' ? (
          <>
            {picker(suffix, weights, setWeights, `把权重文件（${suffix}）拖到这里，或点一下选择`)}
            {effectiveKind === 'ocr_rec' &&
              picker('.txt', dictFile, setDictFile, '识别字典（.txt，一行一个字）：模型自带字符表时不用传')}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              只收 ONNX 和 GGUF。PaddleOCR 训练出来的 Paddle 模型，先在外面用 PaddleOCR 官方的
              <Typography.Text code>paddlex --paddle2onnx</Typography.Text>转成 ONNX。
            </Typography.Text>
          </>
        ) : (
          picker('.zip', archive, setArchive, '把模型库导出的 zip 拖到这里（里面有模型卡 README.md）')
        )}
        <Flex gap={8} wrap>
          <Input
            style={{ flex: 1, minWidth: 200 }}
            value={baseModel}
            onChange={(e) => setBaseModel(e.target.value)}
            placeholder="从哪个模型微调来的（可不填），例如 PP-OCRv6_rec_small"
          />
          <Input
            style={{ width: 160 }}
            value={license}
            onChange={(e) => setLicense(e.target.value)}
            placeholder="许可，例如 apache-2.0"
          />
        </Flex>
        <Select
          mode="tags"
          value={datasets}
          onChange={setDatasets}
          placeholder="训练用了哪些数据集（可不填）"
          options={datasetNames.map((d) => ({ value: d, label: d }))}
        />
        <Input.TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="说明：怎么训练的、改了什么、要注意什么（写进模型卡）"
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
      title={target ? `模型卡：${target.model.name} v${target.version.version}` : ''}
      open={target !== null}
      onCancel={onClose}
      onOk={() => save.mutate()}
      okText="保存"
      confirmLoading={save.isPending}
    >
      <Flex vertical gap={10}>
        <Input value={baseModel} onChange={(e) => setBaseModel(e.target.value)} placeholder="基础模型：从哪个模型微调来的" />
        <Input value={license} onChange={(e) => setLicense(e.target.value)} placeholder="许可，例如 apache-2.0" />
        <Select
          mode="tags"
          value={datasets}
          onChange={setDatasets}
          placeholder="训练用的数据集"
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
  if (checks.format === 'onnx') parts.push('ONNX，onnxruntime 能加载')
  if (typeof checks.characters === 'number') {
    const from = checks.dict_source === 'model' ? '模型自带' : '字典文件'
    parts.push(`字符表 ${checks.characters} 个字（${from}）`)
  }
  if (typeof checks.classes === 'number') parts.push(`输出 ${checks.classes} 类`)
  if (typeof checks.dict_note === 'string') parts.push(checks.dict_note)
  return parts.join('；')
}

function ModelsTab({ canManage, datasetNames }: { canManage: boolean; datasetNames: string[] }) {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const models = useQuery({ queryKey: ['hub-models'], queryFn: listModels })
  const [importing, setImporting] = useState<ImportTarget | null>(null)
  const [editing, setEditing] = useState<{ model: HubModel; version: ModelVersion } | null>(null)
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['hub-models'] })
    void queryClient.invalidateQueries({ queryKey: ['models'] })
  }
  const activate = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) => activateModelVersion(name, version),
    onSuccess: (data) => {
      refresh()
      Modal.info({ title: '已选用', content: `${data.restart_hint}。在终端里运行 uv run jacara stop，再 uv run jacara start。` })
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) => deleteModelVersion(name, version),
    onSuccess: refresh,
    onError: (err) => void message.error(errorText(err)),
  })
  const download = useMutation({
    mutationFn: ({ name, version }: { name: string; version: number }) => downloadModelVersion(name, version),
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
    onError: (err) => void message.error(errorText(err)),
  })
  const data = models.data

  const columns = (model: HubModel): TableProps<ModelVersion>['columns'] => [
    {
      title: '版本',
      key: 'version',
      width: 110,
      render: (_, v) => (
        <Space size={4} wrap>
          <Typography.Text strong>v{v.version}</Typography.Text>
          {v.in_use && (
            <Tag color="green" icon={<CheckCircleOutlined />}>
              在用
            </Tag>
          )}
        </Space>
      ),
    },
    {
      title: '文件',
      key: 'files',
      render: (_, v) => (
        <Flex vertical gap={2}>
          {v.files.map((f) => (
            <Typography.Text key={f.name} style={{ fontSize: 13 }}>
              {f.role === 'dict' ? '字典 ' : ''}
              {f.name}（{formatBytes(f.size)}）{' '}
              <Tooltip title={`SHA-256：${f.sha256}`}>
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
      title: '模型卡',
      key: 'card',
      render: (_, v) => (
        <Flex vertical gap={2} style={{ maxWidth: 360 }}>
          {v.base_model && <Typography.Text style={{ fontSize: 13 }}>基础模型：{v.base_model}</Typography.Text>}
          {v.license && <Typography.Text style={{ fontSize: 13 }}>许可：{v.license}</Typography.Text>}
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
      title: '导入',
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
            width: 260,
            render: (_: unknown, v: ModelVersion) => (
              <Space size={4} wrap>
                <Button
                  size="small"
                  type={v.in_use ? 'default' : 'primary'}
                  disabled={v.in_use || data?.env_locked.includes(v.kind)}
                  loading={activate.isPending && activate.variables?.version === v.version && activate.variables?.name === model.name}
                  onClick={() => activate.mutate({ name: model.name, version: v.version })}
                >
                  用这个版本
                </Button>
                <Tooltip title="打包成 zip（权重 + 模型卡），拿到另一台电脑上导入">
                  <Button size="small" icon={<DownloadOutlined />} onClick={() => download.mutate({ name: model.name, version: v.version })} />
                </Tooltip>
                <Tooltip title="改模型卡">
                  <Button size="small" icon={<EditOutlined />} onClick={() => setEditing({ model, version: v })} />
                </Tooltip>
                <Popconfirm
                  title={`删掉 ${model.name} v${v.version}？`}
                  description="文件会从 models/hub 里删掉，不能恢复。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  disabled={v.in_use}
                  onConfirm={() => remove.mutate({ name: model.name, version: v.version })}
                >
                  <Button size="small" danger icon={<DeleteOutlined />} disabled={v.in_use} />
                </Popconfirm>
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
          模型文件在本机的 models/hub/ 里，每个版本一个文件夹、一张模型卡。"在用"的就是换模型页选的那个；
          换了以后要 jacara stop、jacara start 才生效。
        </Typography.Text>
        {canManage && (
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setImporting({})}>
            导入模型
          </Button>
        )}
      </Flex>
      {data && data.env_locked.length > 0 && (
        <Alert
          type="info"
          showIcon
          title={`.env 里指定了：${data.env_locked.map((k) => data.kinds[k as ModelKind] ?? k).join('、')}。这几种在网页上换不了。`}
        />
      )}
      {models.isError && <Alert type="error" showIcon title="模型库读不出来" description={errorText(models.error)} />}
      {data && data.models.length === 0 && (
        <Empty description="模型库是空的。训练好的 OCR 模型（ONNX）、大模型（GGUF）都可以导入进来。" />
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
                导入新版本
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

// ---- 数据集库 ----

function ImportDatasetDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
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
      void message.success(`已存进数据集库：${d.name} v${d.version}${skipped ? `，跳过了 ${skipped} 行` : ''}`)
      void queryClient.invalidateQueries({ queryKey: ['hub-datasets'] })
      onClose()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <Modal
      title="上传数据集"
      open={open}
      onCancel={onClose}
      width={600}
      footer={[
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button
          key="ok"
          type="primary"
          disabled={!file || !name.trim()}
          loading={submit.isPending}
          onClick={() => submit.mutate()}
        >
          上传并检查
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
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="名字，例如 收据识别。同名再传一次版本号加 1" maxLength={64} />
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
              {file.name}（{formatBytes(file.size)}）
            </Typography.Text>
          ) : (
            <Typography.Text>把 zip 拖到这里（最大 1 GB）</Typography.Text>
          )}
        </Upload.Dragger>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {kind === 'ocr_rec'
            ? '认 PaddleOCR / PaddleX 的识别标注：rec_gt_train.txt、rec_gt_val.txt、rec_gt_test.txt，train.txt、val.txt，或 PPOCRLabel 导出的 rec_gt.txt。每行：图片路径、Tab、文字。'
            : '认 PaddleOCR / PaddleX 的检测标注：det_gt_train.txt、det_gt_val.txt、det_gt_test.txt，train.txt、val.txt，或 PPOCRLabel 的 Label.txt。每行：图片路径、Tab、框的 JSON。'}
        </Typography.Text>
        <Input.TextArea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="数据集卡：从哪来、怎么标的、要注意什么"
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
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [importing, setImporting] = useState(false)
  const download = useMutation({
    mutationFn: downloadDataset,
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
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
      title: '数据集',
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
      title: '种类',
      key: 'kind',
      width: 150,
      render: (_, d) => (
        <Space size={4} wrap>
          <Tag color={d.kind === 'ocr_rec' ? 'blue' : 'purple'}>{d.kind === 'ocr_rec' ? '识别' : '检测'}</Tag>
          <Tag>{SOURCE_NAMES[d.source]}</Tag>
        </Space>
      ),
    },
    {
      title: '各边',
      key: 'counts',
      render: (_, d) => (
        <Flex vertical gap={0}>
          {SPLITS.filter((s) => d.counts[s]).map((s) => (
            <Typography.Text key={s} style={{ fontSize: 13 }}>
              {SPLIT_NAMES[s]} {d.counts[s]} {d.kind === 'ocr_rec' ? '行' : '页'}
              {d.groups[s] ? `、${d.groups[s]} 组` : ''}
            </Typography.Text>
          ))}
          {Object.keys(d.problems).length > 0 && (
            <Tooltip title={Object.entries(d.problems).map(([r, n]) => `${r}：${n}`).join('；')}>
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                跳过 {Object.values(d.problems).reduce((a, b) => a + b, 0)} 行
              </Typography.Text>
            </Tooltip>
          )}
        </Flex>
      ),
    },
    {
      title: '大小',
      key: 'size',
      width: 100,
      render: (_, d) => (
        <Tooltip title={`SHA-256：${d.sha256}`}>
          <span>{formatBytes(d.size)}</span>
        </Tooltip>
      ),
    },
    { title: '时间', key: 'created', width: 150, render: (_, d) => formatTime(d.created_at) },
    {
      title: '',
      key: 'actions',
      width: 200,
      render: (_, d) => (
        <Space size={4} wrap>
          {canChange && (
            <Button size="small" icon={<ExperimentOutlined />} onClick={() => onEvaluate(d)}>
              评测
            </Button>
          )}
          <Button size="small" icon={<DownloadOutlined />} loading={download.isPending && download.variables?.id === d.id} onClick={() => download.mutate(d)} />
          {canChange && (
            <Popconfirm
              title={`删除 ${d.name} v${d.version}？`}
              description="用它做的评测也会一起删掉。"
              okText="删除"
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
          数据集是你的业务数据（单据上的图和字），存在本机的对象存储里，备份里有它。OCR 样本页、检测样本页的导出可以直接存进来。
        </Typography.Text>
        {canChange && (
          <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => setImporting(true)}>
            上传数据集
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
        locale={{ emptyText: <Empty description="还没有数据集" /> }}
      />
      {importing && <ImportDatasetDialog open onClose={() => setImporting(false)} />}
    </Flex>
  )
}

// ---- 评测 ----

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
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState(dataset?.id ?? datasets[0]?.id)
  const chosen = datasets.find((d) => d.id === datasetId)
  const splits = SPLITS.filter((s) => chosen?.counts[s])
  const [split, setSplit] = useState<DatasetSplit | undefined>()
  const effectiveSplit = split && splits.includes(split) ? split : splits.includes('val') ? 'val' : splits[0]
  const slot: ModelKind = chosen?.kind === 'ocr_det' ? 'ocr_det' : 'ocr_rec'
  const options = [
    { value: 'current', label: '现在用的' },
    { value: 'builtin', label: 'RapidOCR 自带的' },
    ...models
      .filter((m) => m.kind === slot)
      .flatMap((m) =>
        m.versions.map((v) => ({
          value: refValue({ source: 'hub', name: m.name, version: v.version }),
          label: `${m.name} v${v.version}${v.in_use ? '（在用）' : ''}`,
        })),
      ),
  ]
  const [a, setA] = useState('current')
  const [b, setB] = useState<string | undefined>()
  const submit = useMutation({
    mutationFn: () => createEval({ dataset_id: datasetId!, split: effectiveSplit!, a: parseRef(a), b: parseRef(b!) }),
    onSuccess: () => {
      void message.success('已放进后台队列，跑完会在下面显示')
      void queryClient.invalidateQueries({ queryKey: ['hub-evals'] })
      onClose()
      onStarted()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <Modal
      title="比较两个 OCR 模型"
      open
      onCancel={onClose}
      onOk={() => submit.mutate()}
      okText="开始评测"
      okButtonProps={{ disabled: !datasetId || !effectiveSplit || !b || a === b }}
      confirmLoading={submit.isPending}
      width={560}
    >
      <Flex vertical gap={12}>
        <Select
          value={datasetId}
          onChange={setDatasetId}
          placeholder="数据集"
          options={datasets.map((d) => ({
            value: d.id,
            label: `${d.name} v${d.version}（${d.kind === 'ocr_rec' ? '识别' : '检测'}）`,
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
          <Select style={{ flex: 1 }} value={b} onChange={setB} options={options} placeholder="要试的模型" />
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {slot === 'ocr_rec'
            ? '识别：每张小图只跑识别，比整行一模一样的比例（Exact Match）和 1 − 平均归一化编辑距离（Char Match），指标照 RapidOCR 作者的 TextRecMetric。'
            : '检测：每张整页图只跑检测，IoU > 0.5 算配上，按 PaddleOCR 的 DetectionIoUEvaluator 算 Precision、Recall、H-mean。'}
          反复看验证集来挑模型，验证集会慢慢"被调过"；正式验收用锁定测试集。
        </Typography.Text>
      </Flex>
    </Modal>
  )
}

function EvalDetail({ run }: { run: EvalRun }) {
  const s = run.summary
  if (!s) {
    return run.error ? <Alert type="error" showIcon title="评测出错了" description={run.error} /> : null
  }
  const [a, b] = run.arms
  const rows =
    s.task === 'rec'
      ? [
          ['Exact Match（整行一模一样）', pct(s.a.exact_match), pct(s.b.exact_match)],
          ['Char Match（1 − 平均归一化编辑距离）', pct(s.a.char_match), pct(s.b.char_match)],
          ['只差空格、大小写、全角半角', s.a.format_only, s.b.format_only],
          ['什么也没认出来', s.a.empty, s.b.empty],
        ]
      : [
          ['Precision', pct(s.a.precision), pct(s.b.precision)],
          ['Recall', pct(s.a.recall), pct(s.b.recall)],
          ['H-mean', pct(s.a.hmean), pct(s.b.hmean)],
          ['检测框 / 配上的', `${s.a.det_boxes} / ${s.a.matched}`, `${s.b.det_boxes} / ${s.b.matched}`],
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
          { title: `A：${a?.label}`, key: 'a', align: 'right', render: (_, r) => r[1] },
          { title: `B：${b?.label}`, key: 'b', align: 'right', render: (_, r) => r[2] },
        ]}
      />
      <Descriptions
        size="small"
        column={1}
        items={[
          {
            key: 'changed',
            label: s.task === 'rec' ? 'B 修好 / 弄坏' : 'B 新配上 / 丢了',
            children: `${s.fixed} / ${s.broken}（精确 McNemar p = ${s.mcnemar_p}）`,
          },
          ...(s.task === 'rec'
            ? [
                {
                  key: 'ci',
                  label: 'Exact Match 差（B − A）95% 区间',
                  children: s.bootstrap_ci
                    ? `${pct(s.bootstrap_ci[0])} ～ ${pct(s.bootstrap_ci[1])}（按业务单据分组 bootstrap${s.grouped ? '' : '；没有组号，每张图自成一组，区间偏窄'}）`
                    : '不到 2 组，不做',
                },
              ]
            : []),
          { key: 'evidence', label: '证据', children: s.evidence_text },
          { key: 'time', label: '用时', children: `${s.seconds} 秒` },
          ...run.arms.map((arm) => ({
            key: arm.name,
            label: `${arm.name} 的文件`,
            children: (
              <Typography.Text style={{ ...MONO, fontSize: 12 }}>
                {(['det', 'rec', 'dict'] as const)
                  .filter((k) => arm[k])
                  .map((k) => `${arm[k]}（${(arm.sha256[k] ?? '').slice(0, 12)}）`)
                  .join('；') || 'RapidOCR 自带的'}
              </Typography.Text>
            ),
          })),
        ]}
      />
    </Flex>
  )
}

function EvalsTab({ canChange, onNew }: { canChange: boolean; onNew: () => void }) {
  const { message } = App.useApp()
  const runs = useQuery({
    queryKey: ['hub-evals'],
    queryFn: listEvals,
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === 'queued' || r.status === 'running') ? 3000 : false),
  })
  const download = useMutation({
    mutationFn: downloadEvalLines,
    onSuccess: ({ blob, filename }) => saveFile(blob, filename),
    onError: (err) => void message.error(errorText(err)),
  })
  const statusTag = (r: EvalRun) =>
    ({
      queued: <Tag>排队中</Tag>,
      running: <Tag color="processing">在跑 {r.progress}</Tag>,
      done: <Tag color="success">完成</Tag>,
      failed: <Tag color="error">出错</Tag>,
    })[r.status]
  const columns: TableProps<EvalRun>['columns'] = [
    { title: '时间', key: 'time', width: 150, render: (_, r) => formatTime(r.created_at) },
    {
      title: '数据集',
      key: 'dataset',
      render: (_, r) => (
        <>
          {r.dataset} · {SPLIT_NAMES[r.split as DatasetSplit] ?? r.split}{' '}
          <Tag color={r.task === 'rec' ? 'blue' : 'purple'}>{r.task === 'rec' ? '识别' : '检测'}</Tag>
        </>
      ),
    },
    {
      title: 'A → B',
      key: 'arms',
      render: (_, r) => `${r.arms[0]?.label} → ${r.arms[1]?.label}`,
    },
    {
      title: '结果',
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
              {{ process: '只能检查流程', thin: '证据偏少', trend: '可以看趋势' }[s.evidence]}
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
          <Tooltip title="下载逐条结果（jsonl，里面有单据上的文字，只放在本机）">
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download.mutate(r.id)} />
          </Tooltip>
        ),
    },
  ]
  return (
    <Flex vertical gap={12}>
      <Flex justify="space-between" align="center" gap={8} wrap>
        <Typography.Text type="secondary">
          在数据集的某一边上比较两个模型（只读，不会换模型）。B 明显更好、证据也够了，再去模型库点"用这个版本"。
        </Typography.Text>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void runs.refetch()} />
          {canChange && (
            <Button type="primary" icon={<ExperimentOutlined />} onClick={onNew}>
              新的评测
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
        locale={{ emptyText: <Empty description="还没有评测" /> }}
      />
    </Flex>
  )
}

/** 模型和数据集（ADR 0059、0060）：导入导出模型和数据集、选用某个版本、比较两个 OCR 模型。 */
export function HubPage() {
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
          模型和数据集
        </Typography.Title>
        <Typography.Text type="secondary">
          训练在系统外做（PaddleOCR）；这里放进来、比一比、选上用。全部离线，不从网上下载。
        </Typography.Text>
      </Flex>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'models',
            label: '模型库',
            children: <ModelsTab canManage={can('system:manage')} datasetNames={datasetNames} />,
          },
          ...(canExport
            ? [
                {
                  key: 'datasets',
                  label: `数据集库 ${datasets.data?.length ?? ''}`,
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
                  label: '评测',
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
