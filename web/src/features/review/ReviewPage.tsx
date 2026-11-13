import {
  ArrowLeftOutlined,
  AppstoreAddOutlined,
  CheckOutlined,
  DeleteOutlined,
  HistoryOutlined,
  DownOutlined,
  LeftOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  UpOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Grid,
  Popconfirm,
  Result,
  Segmented,
  Select,
  Space,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import {
  adoptPrevious,
  completeReview,
  getDocument,
  getNeighbors,
  getPageOcr,
  getPageStructure,
  deleteDocuments,
} from '../../api/documents'
import { getSampleSettings } from '../../api/ocrSamples'
import type { Box, DocumentDetail, Extraction, FieldValue, RevisionIn } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { ExportButton } from '../documents/ExportButton'
import { languageOptions, useLanguage } from '../../utils/languages'
import { StatusTag } from '../documents/StatusTag'
import { FieldDetail, FieldPanel } from './FieldPanel'
import {
  displayLabel,
  evidenceOf,
  neighbor,
  orderFields,
  setTemplateLabels,
  type SortMode,
  splitCell,
} from './fields'
import { PageViewer } from './PageViewer'
import { ReasonDialog, type ReasonRequest } from './ReasonDialog'
import { RegionPanel } from './RegionPanel'
import { TranscriptionPanel } from './TranscriptionPanel'
import { openCommentsOnPage, useComments } from './comments'
import { CommentsButton, SkipButton, SkippedNotice } from './ReviewExtras'
import { ReprocessButton, RunSwitcher } from './RunControls'
import { SaveAsTemplate } from './SaveAsTemplate'
import { useConfirmMany } from './useConfirmMany'
import { useReviewTimer } from './useReviewTimer'
import { useRevise } from './useRevise'
import { useRows } from './useRows'
import { Tip } from '../../components/Tip'
import { ExtractionNotes } from './ExtractionNotes'

/** 下一个还在等确认的字段（从 afterId 后面开始找，找到最后再从头找）。 */
function nextPending(values: FieldValue[], afterId: string | null): string | null {
  const start = values.findIndex((v) => v.id === afterId)
  const ordered = [...values.slice(start + 1), ...values.slice(0, start + 1)]
  return ordered.find((v) => v.pending_review && v.id !== afterId)?.id ?? null
}

const SORT_KEY = 'jacara.review.sort'

function loadSortMode(): SortMode {
  try {
    return localStorage.getItem(SORT_KEY) === 'document' ? 'document' : 'status'
  } catch {
    return 'status' // 浏览器不让用本地存储（隐私模式等）：用默认的
  }
}

function saveSortMode(mode: SortMode) {
  try {
    localStorage.setItem(SORT_KEY, mode)
  } catch {
    // 存不了就算了，只是下次打开要重新选
  }
}

/** 在输入框里打字、或者焦点在按钮上（按 Enter 会点按钮）时，不当作快捷键。 */
function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    target.closest('input, textarea, button, [contenteditable]') !== null
  )
}

/**
 * 审核工作台：左边原图和出处框，右边字段。选中字段可以修改或确认，全部处理完再"完成审核"，
 * 然后自动打开下一份待审核的。
 */
export function ReviewPage() {
  const { id = '' } = useParams()
  // key：换到另一份文档（上一份 / 下一份）时，页码、选中的字段等全部重新开始
  return <ReviewWorkspace key={id} id={id} />
}

function ReviewWorkspace({ id }: { id: string }) {
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  // 原文文字块：工具栏的开关，或者按住 Alt；picked 是按住 Shift / ⌘ 多选的文字块
  const [ocrOn, setOcrOn] = useState(false)
  const [altHeld, setAltHeld] = useState(false)
  const [picked, setPicked] = useState<number[]>([])
  // 开了"自动收集纠错样本"（ADR 0050）：单击的一块原文先核对图中原文再填入
  const [checking, setChecking] = useState<number | null>(null)
  const [reasonRequest, setReasonRequest] = useState<ReasonRequest | null>(null)
  // 框选：模式开关、正在画的框、画好了等着用的框
  const [drawOn, setDrawOn] = useState(false)
  const [draft, setDraft] = useState<Box | null>(null)
  const [drawn, setDrawn] = useState<Box | null>(null)
  // 字段按单据顺序还是按状态（先红后绿）排；记在这个浏览器里，下次打开还是这样
  const [sortMode, setSortMode] = useState<SortMode>(loadSortMode)
  // 窄屏（小于 992 像素）：原图在上、字段在下，上下排；中等宽度时右边窄一点
  const screens = Grid.useBreakpoint()
  const narrow = screens.lg === false
  const sideWidth = screens.xl === false ? 380 : 440

  // 看第几轮的结果；undefined = 现在用的这一轮
  const [run, setRun] = useState<number | undefined>()
  const [savingTemplate, setSavingTemplate] = useState(false)
  const document = useQuery({
    queryKey: ['document', id, run ?? 0],
    queryFn: () => getDocument(id, run),
    // 重新处理中：每 3 秒看一次，处理完自动换成新结果
    refetchInterval: (q) =>
      q.state.data?.status === 'uploaded' || q.state.data?.status === 'processing' ? 3000 : false,
  })
  const doneOrReviewed = document.data?.status === 'done' || document.data?.status === 'reviewed'
  // 重新处理中也能看上一轮的结果（只读）
  const ready = doneOrReviewed || (document.data?.extractions.length ?? 0) > 0
  const ocr = useQuery({
    queryKey: ['page-ocr', id, page],
    queryFn: () => getPageOcr(id, page),
    enabled: ready,
    staleTime: Infinity, // OCR 结果处理完就不会变
  })
  // 版面结构（区域、表格、标题 → 值）：打开"版面结构"时才取
  const [structureOn, setStructureOn] = useState(false)
  const structure = useQuery({
    queryKey: ['page-structure', id, page, run ?? 0],
    queryFn: () => getPageStructure(id, page, run),
    enabled: ready,
    staleTime: Infinity, // 处理完就不会变
  })
  const revise = useRevise(id)
  const comments = useComments(id)
  const rows = useRows(id)
  const confirmMany = useConfirmMany(id)
  // 上一份、下一份（只算待审核的，按列表顺序）
  const neighbors = useQuery({
    queryKey: ['neighbors', id],
    queryFn: () => getNeighbors(id),
    enabled: ready,
  })
  const navigate = useNavigate()
  const goTo = (target: string | null | undefined) => {
    if (target) void navigate(`/documents/${target}`)
  }
  const finish = useMutation({
    mutationFn: () => completeReview(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['document', id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      void queryClient.invalidateQueries({ queryKey: ['neighbors'] })
      // 审完一份，直接打开下一份；没有了就留在这里
      const next = neighbors.data?.next ?? neighbors.data?.previous
      if (next) {
        void message.success(`已完成审核，打开下一份（还有 ${neighbors.data?.remaining ?? 0} 份）`)
        goTo(next)
      } else {
        void message.success('已完成审核。待审核的都审完了')
      }
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const remove = useMutation({
    mutationFn: () => deleteDocuments([id]),
    onSuccess: ({ deleted, skipped }) => {
      if (deleted === 0) {
        void message.warning(skipped[0] ?? '没有删除')
        return
      }
      void message.success('已删除')
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      // 删完打开下一份待审核的；没有了就回列表
      const next = neighbors.data?.next ?? neighbors.data?.previous
      void navigate(next ? `/documents/${next}` : '/documents')
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const viewingOld =
    document.data !== undefined &&
    (document.data.viewing_run ?? 1) !== (document.data.current_run ?? 1)
  // 旧的几轮、正在重新处理的：只能看
  const canEdit = can('value:review') && !viewingOld && doneOrReviewed
  const sampleSettings = useQuery({
    queryKey: ['ocr-sample-settings'],
    queryFn: getSampleSettings,
    staleTime: 60_000,
  })
  const collecting = canEdit && sampleSettings.data?.collect === true
  useReviewTimer(id, canEdit && document.data?.status === 'done')
  // 第 page 页属于哪一张单据（一张单据可以占好几页，ADR 0036）
  const currentExtraction = coveringExtraction(document.data?.extractions ?? [], page)
  // 字段名的语言（ADR 0048）：模板写了几种语言就能切换，按模板记住选的
  const languages = currentExtraction?.languages?.length ? currentExtraction.languages : ['zh']
  const languageKey = `review.${currentExtraction?.template ?? ''}`
  const [language, setLanguage] = useLanguage(languageKey, languages)
  const otherLabels = currentExtraction?.label_sets?.[language]
  // 字段名用这份文档的模板里的（换了文档、换了轮次、换了语言就换）
  setTemplateLabels(
    otherLabels ? { ...currentExtraction?.labels, ...otherLabels } : currentExtraction?.labels,
  )
  const adopt = useMutation({
    mutationFn: () => adoptPrevious(id),
    onSuccess: ({ confirmed, edited }) => {
      void message.success(`沿用了上一轮的审核：确认 ${confirmed} 个，改正 ${edited} 个`)
      void queryClient.invalidateQueries({ queryKey: ['document', id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  // useMemo：数据没变就还是同一个数组，下面的键盘监听不用每次都重新绑
  const values = useMemo(
    () => orderFields(currentExtraction?.values ?? [], sortMode),
    [currentExtraction, sortMode],
  )
  const showOcr = (ocrOn || altHeld) && canEdit && ocr.data?.available === true
  const selected = values.find((v) => v.id === selectedId) ?? null
  // 原图上只画这一页的出处框（几页的单据，别的页的框不画在这页上）
  const pageValues = useMemo(
    () =>
      values.filter((v) => (evidenceOf(v)?.page ?? currentExtraction?.page ?? 1) === page),
    [values, currentExtraction, page],
  )

  /** 用这几块原文填进选中的字段。表格的格子填完跳到下一格（一行商品连着点下去）。 */
  function applyPick(lines: number[]) {
    if (lines.length > 0) {
      applyChange({ action: 'edit', reason: '', source_lines: lines })
    }
  }

  /** 用框选、重新识别并确认过的文字填进选中的字段。transcription：图中原文（收集 OCR 样本时才有）。 */
  function applyBox(box: Box, text: string, reason = '', transcription?: string) {
    applyChange({ action: 'edit', reason, value: text, source_box: box, transcription })
  }

  function applyChange(change: RevisionIn) {
    if (!selected) {
      return
    }
    revise.mutate(
      // 选的是哪一页的原文（几页的单据，ADR 0036）
      { valueId: selected.id, change: { ...change, page } },
      {
        onSuccess: () => {
          setPicked([])
          setChecking(null)
          setDraft(null)
          setDrawn(null)
          if (splitCell(selected.name)) {
            const i = values.findIndex((v) => v.id === selected.id)
            const next = values.slice(i + 1).find((v) => !v.deleted)
            if (next && splitCell(next.name)) {
              setSelectedId(next.id)
            }
          }
        },
      },
    )
  }

  /** 换了选中的字段：多选到一半的原文、画好还没用的框都作废。null = 取消选中。 */
  function select(valueId: string | null) {
    setPicked([])
    setChecking(null)
    setDraft(null)
    setDrawn(null)
    setSelectedId(valueId)
    // 几页的单据：选中的字段出处在别的页，原图翻到那一页
    const target = values.find((v) => v.id === valueId)
    const where = target ? evidenceOf(target)?.page : undefined
    if (where && where !== page) setPage(where)
  }

  function pickLine(index: number, multi: boolean) {
    if (!selected) {
      void message.info('先在右边选中要填的字段，再点原图上的文字')
      return
    }
    if (selected.deleted) {
      void message.info('这一行已经删除了，要改的话先恢复这一行')
      return
    }
    if (multi) {
      setChecking(null)
      setPicked((p) => (p.includes(index) ? p.filter((i) => i !== index) : [...p, index]))
    } else if (collecting && picked.length === 0) {
      setChecking(index) // 先核对图中原文，回车再填入
    } else {
      applyPick([...picked.filter((i) => i !== index), index])
    }
  }

  /** 确认选中的字段（值是对的），然后跳到下一个等确认的。 */
  function confirmSelected() {
    if (!selected || selected.deleted || revise.isPending) {
      return
    }
    const current = selected
    revise.mutate(
      { valueId: current.id, change: { action: 'confirm', reason: '' } },
      { onSuccess: () => setSelectedId(nextPending(values, current.id)) },
    )
  }

  function askClear(v: FieldValue, label: string) {
    setReasonRequest({
      title: `清空「${label}」`,
      description: '原文里没有这个值。原值会保留在修改历史里。',
      placeholder: '比如：收据上没有单号',
      okText: '清空',
      danger: true,
      onOk: (reason) =>
        revise.mutate({ valueId: v.id, change: { action: 'edit', value: null, reason } }),
    })
  }

  function addRow(table: string, after: number | null) {
    if (!currentExtraction) {
      return
    }
    rows.mutate(
      { kind: 'add', extractionId: currentExtraction.id, table, after },
      { onSuccess: (cells) => cells[0] && setSelectedId(cells[0].id) }, // 选中新行第一格，接着点原文填
    )
  }

  // 键盘监听里用最新的这几个函数，又不用每次渲染都重新绑定监听
  const actions = useRef({ applyPick, addRow, askClear, confirmSelected, select })
  useEffect(() => {
    actions.current = { applyPick, addRow, askClear, confirmSelected, select }
  })

  // 在空白处点一下：取消选中，上面的详情收起来。点原图、字段、商品明细、详情本身，
  // 以及弹出的对话框、菜单、提示（它们挂在页面最外层）都不算
  useEffect(() => {
    function onDown(e: PointerEvent) {
      const target = e.target as Element | null
      if (
        !target?.closest ||
        target.closest(
          '[data-keep-selection], [data-value-id], .ant-modal-root, .ant-popover, .ant-dropdown, .ant-tooltip, .ant-message, .ant-select-dropdown',
        )
      ) {
        return
      }
      actions.current.select(null)
    }
    window.addEventListener('pointerdown', onDown)
    return () => window.removeEventListener('pointerdown', onDown)
  }, [])

  // 按住 Alt（Mac 上是 Option）时显示原文文字块，松开就收起
  useEffect(() => {
    const down = (e: KeyboardEvent) => e.key === 'Alt' && setAltHeld(true)
    const up = (e: KeyboardEvent) => e.key === 'Alt' && setAltHeld(false)
    const blur = () => setAltHeld(false) // 按着 Alt 切到别的窗口，回来时不会一直开着
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [])

  // 键盘：↑↓ 切换字段，Enter 确认（多选了原文就是填入），Esc 取消，Delete 清空，
  // ⌘/Ctrl + Enter 在选中的这一行下面插入一行。审核员一天看几百张，鼠标太慢
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target) || values.length === 0 || reasonRequest) {
        return
      }
      const index = values.findIndex((v) => v.id === selectedId)
      if (
        e.key === 'ArrowDown' ||
        e.key === 'ArrowUp' ||
        e.key === 'ArrowLeft' ||
        e.key === 'ArrowRight'
      ) {
        // 普通字段一个一个走；商品明细 ↑↓ 按行走（同一列），←→ 在一行里左右走
        const next = neighbor(values, selectedId, e.key)
        if (next) {
          e.preventDefault()
          actions.current.select(next)
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault() // 到头了：不要让整个页面滚动
        }
      } else if (e.key === 'Escape') {
        if (drawn || draft) {
          setDraft(null)
          setDrawn(null)
        } else if (checking !== null) {
          setChecking(null)
        } else if (picked.length > 0) {
          setPicked([])
        } else {
          setSelectedId(null)
        }
      } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && canEdit && index !== -1) {
        e.preventDefault()
        const cell = splitCell(values[index].name)
        if (cell) {
          actions.current.addRow(cell.table, cell.index)
        }
      } else if (e.key === 'Enter' && picked.length > 0 && canEdit) {
        e.preventDefault()
        actions.current.applyPick(picked)
      } else if (
        (e.key === 'Delete' || e.key === 'Backspace') &&
        canEdit &&
        index !== -1 &&
        !values[index].deleted &&
        values[index].current_value !== null
      ) {
        e.preventDefault()
        actions.current.askClear(values[index], displayLabel(values, values[index]))
      } else if (e.key === 'Enter' && canEdit && index !== -1) {
        e.preventDefault()
        actions.current.confirmSelected()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [values, selectedId, canEdit, revise, picked, checking, reasonRequest, drawn, draft])

  const back = (
    <Link to="/documents">
      <Button icon={<ArrowLeftOutlined />}>返回列表</Button>
    </Link>
  )

  if (document.isPending) {
    return (
      <Flex justify="center" style={{ padding: 96 }}>
        <Spin size="large" />
      </Flex>
    )
  }
  if (document.error) {
    const message =
      document.error instanceof ApiError ? document.error.message : String(document.error)
    return <Result status="warning" title="打不开这个文档" subTitle={message} extra={back} />
  }

  const doc = document.data
  if (!ready) {
    return (
      <Result
        status="info"
        title="还没有结果"
        subTitle="文档处理完（待审核）之后才能在这里看。"
        extra={back}
      />
    )
  }

  const extraction = coveringExtraction(doc.extractions, page)
  const pendingTotal = doc.extractions.reduce(
    (sum, e) => sum + e.values.filter((v) => v.pending_review).length,
    0,
  )
  const changePage = (next: number) => {
    setPage(next)
    setSelectedId(null)
    setPicked([])
    setChecking(null)
  }

  const pagePending = values.filter((v) => v.pending_review).map((v) => v.id)
  // 原文一致、规则通过、还没人确认的（"批量确认已验证 + 通过"）
  const pageVerified = values
    .filter(
      (v) =>
        !v.reviewed &&
        !v.deleted &&
        v.current_value !== null &&
        v.evidence_state === 'verified' &&
        v.check_state === 'ok',
    )
    .map((v) => v.id)

  // "完成审核"按钮：为什么现在不能点
  const finishBlocked = !can('document:review')
    ? '你没有"完成审核"的权限，请找管理员开通'
    : pendingTotal > 0
      ? `还有 ${pendingTotal} 个字段等待确认`
      : null

  const discover = doc.extractions.some((e) => e.template === 'discover')
  const toolbar = (
    <Flex gap={8} wrap justify="start">
      <RunSwitcher
        doc={doc}
        onChange={(next) => {
          setRun(next)
          select(null)
        }}
      />
      {canEdit && (doc.suggestions ?? 0) > 0 && (
        <Popconfirm
          title={`沿用上一轮的 ${doc.suggestions} 个审核？`}
          description="上次人审核过、这次值和位置都对得上的字段：一样的直接确认，上次改过的改成上次的值。"
          okText="沿用"
          cancelText="取消"
          onConfirm={() => adopt.mutate()}
        >
          <Button icon={<HistoryOutlined />} loading={adopt.isPending}>
            采纳上次的确认（{doc.suggestions}）
          </Button>
        </Popconfirm>
      )}
      {discover && canEdit && can('template:manage') && (
        <Button type="primary" ghost icon={<AppstoreAddOutlined />} onClick={() => setSavingTemplate(true)}>
          存成模板
        </Button>
      )}
      {!viewingOld && can('template:manage') && (
        <Tooltip title="在这份单据的原图上框出要的值，建一个新模板（ADR 0047）">
          <Button icon={<AppstoreAddOutlined />} onClick={() => navigate(`/templates/from-boxes/${doc.id}`)}>
            框选建模板
          </Button>
        </Tooltip>
      )}
      {!viewingOld && can('document:process') && <ReprocessButton doc={doc} />}
      {doc.status === 'done' && !viewingOld && !doc.skipped_at && can('document:review') && (
        <SkipButton doc={doc} next={() => goTo(neighbors.data?.next ?? neighbors.data?.previous)} />
      )}
      {/* 字段的备注记在这张单据的第一页（几页的单据，翻到哪页都看得到） */}
      <CommentsButton doc={doc} selected={selected} page={extraction?.page ?? page} />
      {doc.status === 'done' && !viewingOld && (
        <Tooltip title={finishBlocked}>
          <Button
            type="primary"
            icon={<SafetyCertificateOutlined />}
            disabled={finishBlocked !== null}
            loading={finish.isPending}
            onClick={() => finish.mutate()}
          >
            完成审核
          </Button>
        </Tooltip>
      )}
      {/* 一直显示；没有标黄的（等你确认的）就是灰的，鼠标停上去说明为什么 */}
      {canEdit && (
        <Popconfirm
          title={`这张单据 ${pagePending.length} 个标黄的都是对的？`}
          description="不用一个一个点。每个都会记一条确认记录，可以在修改历史里看到。"
          okText="全部确认"
          cancelText="取消"
          disabled={pagePending.length === 0}
          onConfirm={() => confirmMany.mutate(pagePending)}
        >
          <Tooltip
            title={pagePending.length === 0 ? '这张单据没有标黄（等你确认）的了' : undefined}
          >
            <Button
              icon={<CheckOutlined />}
              loading={confirmMany.isPending}
              disabled={pagePending.length === 0}
            >
              全部确认（{pagePending.length}）
            </Button>
          </Tooltip>
        </Popconfirm>
      )}
      {/* 原文一致、规则也通过的（绿的）还没人点过确认：一次确认掉，审核记录里留下"人看过" */}
      {canEdit && (
        <Popconfirm
          title={`确认这张单据 ${pageVerified.length} 个原文一致、规则通过的字段？`}
          description="它们和原文完全一致、规则也没问题，确认后审核记录里会留下“人看过”。"
          okText="确认"
          cancelText="取消"
          disabled={pageVerified.length === 0}
          onConfirm={() => confirmMany.mutate(pageVerified)}
        >
          <Tooltip
            title={
              pageVerified.length === 0 ? '这张单据原文一致、规则通过的都确认过了' : undefined
            }
          >
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={confirmMany.isPending}
              disabled={pageVerified.length === 0}
            >
              确认没问题的（{pageVerified.length}）
            </Button>
          </Tooltip>
        </Popconfirm>
      )}
      <ExportButton ids={[doc.id]} languages={languages} language={language} onLanguage={setLanguage} />
      {can('document:delete') && doc.status !== 'processing' && (
        <Popconfirm
          title={`删除「${doc.filename}」？`}
          description="原始文件、处理结果、审核记录、备注都会删掉，不能恢复。删完打开下一份。"
          okText="删除"
          okButtonProps={{ danger: true }}
          cancelText="取消"
          onConfirm={() => remove.mutate()}
        >
          <Button danger icon={<DeleteOutlined />} loading={remove.isPending}>
            删除
          </Button>
        </Popconfirm>
      )}
      {back}
    </Flex>
  )

  return (
    <Card
      title={
        <Flex align="center" gap={12} wrap>
          <Space.Compact>
            <Tooltip title="上一份待审核的">
              <Button
                icon={<LeftOutlined />}
                aria-label="上一份"
                disabled={!neighbors.data?.previous}
                onClick={() => goTo(neighbors.data?.previous)}
              />
            </Tooltip>
            <Tooltip
              title={
                neighbors.data
                  ? `下一份待审核的（除了这份还有 ${neighbors.data.remaining} 份）`
                  : '下一份待审核的'
              }
            >
              <Button
                icon={<RightOutlined />}
                aria-label="下一份"
                disabled={!neighbors.data?.next}
                onClick={() => goTo(neighbors.data?.next)}
              />
            </Tooltip>
          </Space.Compact>
          <Typography.Text strong ellipsis style={{ maxWidth: narrow ? 200 : 360 }}>
            {doc.filename}
          </Typography.Text>
          <StatusTag status={doc.status} />
        </Flex>
      }
      // 按钮多：放到标题下面一行，不和文件名挤在一起
      styles={{ body: { paddingTop: 16 } }}
    >
      <div style={{ marginBottom: 12 }}>{toolbar}</div>
      <SkippedNotice doc={doc} canUndo={can('document:review') && !viewingOld} />
      {!doneOrReviewed && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title={doc.status === 'failed' ? '重新处理失败了，下面是上一轮的结果' : '正在重新处理'}
          description={
            doc.status === 'failed'
              ? doc.error
              : `${doc.stage ?? '排队中'}${doc.progress !== null ? `（${doc.progress}%）` : ''}。下面是上一轮的结果，只能查看；处理完会自动换成新结果。`
          }
        />
      )}
      {viewingOld && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title={`这是第 ${doc.viewing_run} 轮的旧结果，只能查看`}
          action={
            <Button size="small" onClick={() => setRun(undefined)}>
              回到现在的结果
            </Button>
          }
        />
      )}
      {discover && !viewingOld && (
        <Tip
          id="review-discover"
          style={{ marginBottom: 12 }}
          title="这份没有用模板，抽出了单据上所有的键值对"
          description='看一下哪些是要的、值对不对，然后点"存成模板"：选字段、起中文名，以后同类单据直接用这个模板。'
        />
      )}
      {currentExtraction?.source_notes?.map((note) => (
        <Alert key={note} type="warning" showIcon style={{ marginBottom: 12 }} title={note} />
      ))}
      <ExtractionNotes extraction={currentExtraction} documentId={id} />
      <PageTabs doc={doc} page={page} onChange={changePage} />
      <Flex
        gap={narrow ? 16 : 24}
        vertical={narrow}
        align="stretch"
        style={narrow ? undefined : { height: 'calc(100vh - 280px)', minHeight: 480 }}
      >
        <div
          style={narrow ? { height: '60vh', minHeight: 360 } : { flex: 1, minWidth: 0 }}
          data-keep-selection
        >
          {drawn && selected && (
            <RegionPanel
              key={drawn.join(',')} // 换了框，重新识别的结果清空
              documentId={doc.id}
              page={page}
              box={drawn}
              label={displayLabel(values, selected)}
              lines={ocr.data?.lines ?? []}
              busy={revise.isPending}
              onUseLines={(indexes) => applyPick(indexes)}
              type={selected.type}
              collecting={collecting}
              onUseText={(text, byModel, transcription) =>
                applyBox(drawn, text, byModel ? '模型看原图读的，人核对过' : '', transcription)
              }
              onCancel={() => {
                setDraft(null)
                setDrawn(null)
              }}
            />
          )}
          {drawn && !selected && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8 }}
              title="框好了。先在右边选中要填的字段"
            />
          )}
          {checking !== null && selected && (
            <TranscriptionPanel
              key={checking} // 换了一块原文，输入框重新开始
              ocrText={ocr.data?.lines[checking]?.text ?? ''}
              label={displayLabel(values, selected)}
              busy={revise.isPending}
              onUse={(transcription) =>
                applyChange({ action: 'edit', reason: '', source_lines: [checking], transcription })
              }
              onCancel={() => setChecking(null)}
            />
          )}
          {picked.length > 0 && selected && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 8 }}
              title={`已选 ${picked.length} 块原文：「${picked
                .map((i) => ocr.data?.lines[i]?.text ?? '')
                .join(' ')}」`}
              action={
                <Flex gap={8}>
                  <Button
                    size="small"
                    type="primary"
                    loading={revise.isPending}
                    onClick={() => applyPick(picked)}
                  >
                    填入「{displayLabel(values, selected)}」（Enter）
                  </Button>
                  <Button size="small" onClick={() => setPicked([])}>
                    取消（Esc）
                  </Button>
                </Flex>
              }
            />
          )}
          <PageViewer
            documentId={doc.id}
            page={page}
            values={pageValues}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={select}
            onHover={setHoveredId}
            ocr={ocr.data}
            structure={structure.data}
            showStructure={structureOn}
            onToggleStructure={() => setStructureOn((on) => !on)}
            showOcr={showOcr}
            onToggleOcr={() => setOcrOn((on) => !on)}
            picked={checking !== null ? [checking] : picked}
            onPickLine={pickLine}
            canDraw={canEdit}
            drawOn={drawOn}
            onToggleDraw={() => {
              setDrawOn((on) => !on)
              setDraft(null)
              setDrawn(null)
            }}
            draft={draft}
            onDraft={(box) => {
              setDraft(box)
              setDrawn(null) // 又在拖了：上一个框作废
            }}
            onDraftDone={setDrawn}
            extra={
              <>
                {/* 上一项、下一项：和键盘 ↑↓ 一样（商品明细里是上一行、下一行的同一列） */}
                <Tooltip title="上一项（↑）">
                  <Button
                    size="small"
                    icon={<UpOutlined />}
                    aria-label="上一项"
                    disabled={!neighbor(values, selectedId, 'ArrowUp')}
                    onClick={() => {
                      const prev = neighbor(values, selectedId, 'ArrowUp')
                      if (prev) select(prev)
                    }}
                  />
                </Tooltip>
                <Tooltip title={selected ? '下一项（↓）' : '从第一项开始（↓）'}>
                  <Button
                    size="small"
                    icon={<DownOutlined />}
                    aria-label="下一项"
                    disabled={!neighbor(values, selectedId, 'ArrowDown')}
                    onClick={() => {
                      const next = neighbor(values, selectedId, 'ArrowDown')
                      if (next) select(next)
                    }}
                  />
                </Tooltip>
                {canEdit && (
                <Tooltip title="选中的字段值是对的（Enter）">
                  <Button
                    size="small"
                    type={selected?.pending_review ? 'primary' : 'default'}
                    icon={<CheckOutlined />}
                    disabled={!selected || selected.deleted}
                    loading={revise.isPending && revise.variables?.change.action === 'confirm'}
                    onClick={confirmSelected}
                  >
                    确认
                  </Button>
                </Tooltip>
                )}
              </>
            }
          />
        </div>
        {/* 右边分上下两块：上面是选中字段的详情（固定，不用往下翻），下面是字段和商品明细（自己滚动） */}
        <Flex
          vertical
          gap={8}
          style={narrow ? undefined : { width: sideWidth, flexShrink: 0, minHeight: 0 }}
        >
          {selected && (
            <div
              data-keep-selection
              style={{ maxHeight: '55%', overflow: 'auto', flexShrink: 0, paddingRight: 4 }}
            >
              <FieldDetail
                documentId={doc.id}
                value={selected}
                label={displayLabel(values, selected)}
                canEdit={canEdit}
                revise={revise}
                onReviewed={() => setSelectedId(nextPending(values, selected.id))}
                onClear={() => askClear(selected, displayLabel(values, selected))}
                onClose={() => select(null)}
                onAdjustBox={() => {
                  // 打开框选，从这个字段现在的框开始：可以直接"重新识别这块"，也可以先拖到对的位置
                  const box = (selected.current_evidence ?? selected.evidence)?.bbox ?? null
                  setPicked([])
                  setDrawOn(true)
                  setDraft(box)
                  setDrawn(box)
                  if (!box) {
                    void message.info('在左边原图上拖出一个框')
                  }
                }}
              />
            </div>
          )}
          {languages.length > 1 && (
            <Flex align="center" gap={8} style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary">字段名</Typography.Text>
              <Select
                data-keep-selection
                size="small"
                aria-label="字段名的语言"
                value={language}
                options={languageOptions(languages)}
                onChange={setLanguage}
                style={{ minWidth: 140 }}
              />
            </Flex>
          )}
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', paddingRight: 4 }}>
          {extraction ? (
            <FieldPanel
              extraction={{ ...extraction, values }}
              sortMode={sortMode}
              comments={openCommentsOnPage(comments.data, extraction.page)}
              onSortMode={(mode) => {
                setSortMode(mode)
                saveSortMode(mode)
              }}
              selectedId={selectedId}
              hoveredId={hoveredId}
              onSelect={select}
              onHover={setHoveredId}
              canEdit={canEdit}
              onClear={askClear}
              onConfirmMany={(ids) => confirmMany.mutate(ids)}
              onAddRow={addRow}
              onDeleteRow={(table, row) =>
                setReasonRequest({
                  title: `删除第 ${row.display} 行`,
                  description:
                    '这一行变成灰色、划掉，不算进商品合计，导出时也没有。可以随时恢复。',
                  placeholder: '比如：这是会员号，不是商品',
                  okText: '删除',
                  danger: true,
                  onOk: (reason) =>
                    rows.mutate(
                      {
                        kind: 'delete',
                        extractionId: extraction.id,
                        table,
                        index: row.index,
                        reason,
                      },
                      { onSuccess: () => select(null) }, // 选中的格子可能就在删掉的这一行
                    ),
                })
              }
              onRestoreRow={(table, row) =>
                setReasonRequest({
                  title: '恢复这一行',
                  description: '恢复成删除之前的值。',
                  placeholder: '比如：删错了',
                  okText: '恢复',
                  onOk: (reason) =>
                    rows.mutate({
                      kind: 'restore',
                      extractionId: extraction.id,
                      table,
                      index: row.index,
                      reason,
                    }),
                })
              }
            />
          ) : (
            <Empty description="这一页没有抽取结果" />
          )}
          </div>
        </Flex>
      </Flex>
      <ReasonDialog request={reasonRequest} onClose={() => setReasonRequest(null)} />
      {savingTemplate && (
        <SaveAsTemplate doc={doc} open={savingTemplate} onClose={() => setSavingTemplate(false)} />
      )}
    </Card>
  )
}


/** 第 page 页属于哪一张单据（哪一条抽取记录）。 */
function coveringExtraction(extractions: Extraction[], page: number): Extraction | undefined {
  return extractions.find((e) => e.page <= page && page <= (e.last_page ?? e.page))
}

/**
 * 翻页（ADR 0036）：一个文件有几张单据时，上面一排是单据（"单据 2 · 第 3–4 页"），
 * 选中的单据有好几页时，下面一排是它的页。只有一张一页的单据就什么都不显示。
 */
function PageTabs({
  doc,
  page,
  onChange,
}: {
  doc: DocumentDetail
  page: number
  onChange: (page: number) => void
}) {
  const parts = doc.extractions
  const current = coveringExtraction(parts, page)
  const range = (e: Extraction) =>
    (e.last_page ?? e.page) === e.page ? `第 ${e.page} 页` : `第 ${e.page}–${e.last_page} 页`
  const pages = current ? (current.last_page ?? current.page) - current.page + 1 : doc.page_count
  if (parts.length <= 1 && pages <= 1 && doc.page_count <= 1) return null
  return (
    <Flex vertical gap={8} style={{ marginBottom: 16 }}>
      {parts.length > 1 && (
        <Segmented
          value={current?.id}
          onChange={(id) => {
            const e = parts.find((x) => x.id === id)
            if (e) onChange(e.page)
          }}
          options={parts.map((e, i) => {
            const pending = e.values.filter((v) => v.pending_review).length
            return {
              value: e.id,
              label: `单据 ${i + 1} · ${range(e)}${pending ? `（${pending} 待确认）` : ''}`,
            }
          })}
        />
      )}
      {pages > 1 && current && (
        <Segmented
          size="small"
          value={page}
          onChange={(value) => onChange(Number(value))}
          options={Array.from({ length: pages }, (_, i) => ({
            label: `第 ${current.page + i} 页`,
            value: current.page + i,
          }))}
        />
      )}
      {!current && doc.page_count > 1 && (
        <Segmented
          value={page}
          onChange={(value) => onChange(Number(value))}
          options={Array.from({ length: doc.page_count }, (_, i) => ({
            label: `第 ${i + 1} 页`,
            value: i + 1,
          }))}
        />
      )}
    </Flex>
  )
}
