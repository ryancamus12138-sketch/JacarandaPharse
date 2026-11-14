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
import { useTranslation } from 'react-i18next'

/** Next field awaiting confirmation (searching after afterId and wrapping around). */
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
    return 'status' // Local storage unavailable (private mode and similar): use the default.
  }
}

function saveSortMode(mode: SortMode) {
  try {
    localStorage.setItem(SORT_KEY, mode)
  } catch {
    // Storage unavailable: ignore; the choice must be made again next time.
  }
}

/** Not a shortcut while typing in an input or while a button has focus (Enter would click it). */
function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    target.closest('input, textarea, button, [contenteditable]') !== null
  )
}

/**
 * Review workspace: page image with evidence boxes on the left, fields on the right. Selected fields can be
 * edited or confirmed; once all are handled, "Complete review" opens the next document awaiting review.
 */
export function ReviewPage() {
  const { id = '' } = useParams()
  // key: switching to another document (previous / next) resets page, selected field and everything else.
  return <ReviewWorkspace key={id} id={id} />
}

function ReviewWorkspace({ id }: { id: string }) {
  const { t } = useTranslation()
  const { can } = useAuth()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  // Source text blocks: toolbar toggle or holding Alt; picked holds blocks multi-selected with Shift / ⌘.
  const [ocrOn, setOcrOn] = useState(false)
  const [altHeld, setAltHeld] = useState(false)
  const [picked, setPicked] = useState<number[]>([])
  // With OCR sample collection enabled, a single clicked source block is first checked against the
  // transcription before filling.
  const [checking, setChecking] = useState<number | null>(null)
  const [reasonRequest, setReasonRequest] = useState<ReasonRequest | null>(null)
  // Region selection: mode toggle, box being drawn, drawn box awaiting use.
  const [drawOn, setDrawOn] = useState(false)
  const [draft, setDraft] = useState<Box | null>(null)
  const [drawn, setDrawn] = useState<Box | null>(null)
  // Fields ordered by document order or by status (red first, green last); remembered in this browser.
  const [sortMode, setSortMode] = useState<SortMode>(loadSortMode)
  // Narrow screens (below 992 pixels): image above, fields below; at medium widths the right column is
  // narrower.
  const screens = Grid.useBreakpoint()
  const narrow = screens.lg === false
  const sideWidth = screens.xl === false ? 380 : 440

  // Run to show; undefined = the current run.
  const [run, setRun] = useState<number | undefined>()
  const [savingTemplate, setSavingTemplate] = useState(false)
  const document = useQuery({
    queryKey: ['document', id, run ?? 0],
    queryFn: () => getDocument(id, run),
    // While reprocessing: poll every 3 seconds and switch to the new results when done.
    refetchInterval: (q) =>
      q.state.data?.status === 'uploaded' || q.state.data?.status === 'processing' ? 3000 : false,
  })
  const doneOrReviewed = document.data?.status === 'done' || document.data?.status === 'reviewed'
  // While reprocessing, the previous run's results remain viewable (read-only).
  const ready = doneOrReviewed || (document.data?.extractions.length ?? 0) > 0
  const ocr = useQuery({
    queryKey: ['page-ocr', id, page],
    queryFn: () => getPageOcr(id, page),
    enabled: ready,
    staleTime: Infinity, // OCR results never change once processed.
  })
  // Layout structure (regions, tables, label → value pairs): fetched only when the layout view is opened.
  const [structureOn, setStructureOn] = useState(false)
  const structure = useQuery({
    queryKey: ['page-structure', id, page, run ?? 0],
    queryFn: () => getPageStructure(id, page, run),
    enabled: ready,
    staleTime: Infinity, // Never changes once processed.
  })
  const revise = useRevise(id)
  const comments = useComments(id)
  const rows = useRows(id)
  const confirmMany = useConfirmMany(id)
  // Previous and next (documents awaiting review only, in list order).
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
      // After completing a review, open the next document; stay here if there is none.
      const next = neighbors.data?.next ?? neighbors.data?.previous
      if (next) {
        void message.success(t('已完成审核，已打开下一份（剩余 {{value}} 份）', { value: neighbors.data?.remaining ?? 0 }))
        goTo(next)
      } else {
        void message.success(t('已完成审核。所有待审核文档均已处理'))
      }
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const remove = useMutation({
    mutationFn: () => deleteDocuments([id]),
    onSuccess: ({ deleted, skipped }) => {
      if (deleted === 0) {
        void message.warning(skipped[0] ?? t('未删除'))
        return
      }
      void message.success(t('已删除'))
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      // After deleting, open the next document awaiting review; return to the list if there is none.
      const next = neighbors.data?.next ?? neighbors.data?.previous
      void navigate(next ? `/documents/${next}` : '/documents')
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const viewingOld =
    document.data !== undefined &&
    (document.data.viewing_run ?? 1) !== (document.data.current_run ?? 1)
  // Earlier runs and documents being reprocessed are read-only.
  const canEdit = can('value:review') && !viewingOld && doneOrReviewed
  const sampleSettings = useQuery({
    queryKey: ['ocr-sample-settings'],
    queryFn: getSampleSettings,
    staleTime: 60_000,
  })
  const collecting = canEdit && sampleSettings.data?.collect === true
  useReviewTimer(id, canEdit && document.data?.status === 'done')
  // Document containing page page (a document may span several pages).
  const currentExtraction = coveringExtraction(document.data?.extractions ?? [], page)
  // Field name language: switchable among the languages defined in the template; remembered per template.
  const languages = currentExtraction?.languages?.length ? currentExtraction.languages : ['zh']
  const languageKey = `review.${currentExtraction?.template ?? ''}`
  const [language, setLanguage] = useLanguage(languageKey, languages)
  const otherLabels = currentExtraction?.label_sets?.[language]
  // Field names come from this document's template (updated when the document, run or language changes).
  setTemplateLabels(
    otherLabels ? { ...currentExtraction?.labels, ...otherLabels } : currentExtraction?.labels,
  )
  const adopt = useMutation({
    mutationFn: () => adoptPrevious(id),
    onSuccess: ({ confirmed, edited }) => {
      void message.success(t('已沿用上一轮审核：确认 {{confirmed}} 个，更正 {{edited}} 个', { confirmed, edited }))
      void queryClient.invalidateQueries({ queryKey: ['document', id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  // useMemo: the same array while data is unchanged, so the keyboard listener below is not rebound on every
  // render.
  const values = useMemo(
    () => orderFields(currentExtraction?.values ?? [], sortMode),
    [currentExtraction, sortMode],
  )
  const showOcr = (ocrOn || altHeld) && canEdit && ocr.data?.available === true
  const selected = values.find((v) => v.id === selectedId) ?? null
  // Draw only this page's evidence boxes on the image (for multi-page documents, other pages' boxes are
  // omitted).
  const pageValues = useMemo(
    () =>
      values.filter((v) => (evidenceOf(v)?.page ?? currentExtraction?.page ?? 1) === page),
    [values, currentExtraction, page],
  )

  /**
   * Fill the selected field with these source blocks. For table cells, move to the next cell afterwards (to
   * fill a row by consecutive clicks).
   */
  function applyPick(lines: number[]) {
    if (lines.length > 0) {
      applyChange({ action: 'edit', reason: '', source_lines: lines })
    }
  }

  /**
   * Fill the selected field with text from region selection and re-recognition, confirmed by the reviewer.
   * transcription: the transcription (only when collecting OCR samples).
   */
  function applyBox(box: Box, text: string, reason = '', transcription?: string) {
    applyChange({ action: 'edit', reason, value: text, source_box: box, transcription })
  }

  function applyChange(change: RevisionIn) {
    if (!selected) {
      return
    }
    revise.mutate(
      // Page of the selected source text (multi-page documents).
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

  /**
   * Selected field changed: discard partially multi-selected source text and drawn but unused boxes. null =
   * deselect.
   */
  function select(valueId: string | null) {
    setPicked([])
    setChecking(null)
    setDraft(null)
    setDrawn(null)
    setSelectedId(valueId)
    // Multi-page documents: if the selected field's evidence is on another page, switch the image to that
    // page.
    const target = values.find((v) => v.id === valueId)
    const where = target ? evidenceOf(target)?.page : undefined
    if (where && where !== page) setPage(where)
  }

  function pickLine(index: number, multi: boolean) {
    if (!selected) {
      void message.info(t('请先在右侧选择要填写的字段，再点击原图中的文字'))
      return
    }
    if (selected.deleted) {
      void message.info(t('此行已删除，如需修改请先恢复'))
      return
    }
    if (multi) {
      setChecking(null)
      setPicked((p) => (p.includes(index) ? p.filter((i) => i !== index) : [...p, index]))
    } else if (collecting && picked.length === 0) {
      setChecking(index) // Check the transcription first; Enter fills it in.
    } else {
      applyPick([...picked.filter((i) => i !== index), index])
    }
  }

  /** Confirm the selected field (its value is correct), then move to the next field awaiting confirmation. */
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
      title: t('清空「{{label}}」', { label }),
      description: t('原文中没有此值。原值将保留在修改历史中。'),
      placeholder: t('例如：收据上没有单号'),
      okText: t('清空'),
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
      { onSuccess: (cells) => cells[0] && setSelectedId(cells[0].id) }, // Select the first cell of the new row and continue filling from source text.
    )
  }

  // The keyboard listener uses the latest functions without being rebound on every render.
  const actions = useRef({ applyPick, addRow, askClear, confirmSelected, select })
  useEffect(() => {
    actions.current = { applyPick, addRow, askClear, confirmSelected, select }
  })

  // Clicking empty space deselects and collapses the details above. Clicks on the image, fields, line items
  // and the details panel,
  // as well as on dialogs, menus and popovers (mounted at the page root), do not count.
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

  // Holding Alt (Option on Mac) shows source text blocks; releasing hides them.
  useEffect(() => {
    const down = (e: KeyboardEvent) => e.key === 'Alt' && setAltHeld(true)
    const up = (e: KeyboardEvent) => e.key === 'Alt' && setAltHeld(false)
    const blur = () => setAltHeld(false) // Switching windows while holding Alt does not leave them shown on return.
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [])

  // Keyboard: ↑↓ switch fields, Enter confirms (or fills multi-selected source text), Esc cancels, Delete
  // clears,
  // ⌘/Ctrl + Enter inserts a row below the selected row. Reviewers handle hundreds of documents a day; the
  // mouse is too slow.
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
        // Regular fields one by one; in line items ↑↓ move by row (same column) and ←→ within a row.
        const next = neighbor(values, selectedId, e.key)
        if (next) {
          e.preventDefault()
          actions.current.select(next)
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault() // At the boundary: do not scroll the whole page.
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
      <Button icon={<ArrowLeftOutlined />}>{t('返回列表')}</Button>
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
    return <Result status="warning" title={t('无法打开此文档')} subTitle={message} extra={back} />
  }

  const doc = document.data
  if (!ready) {
    return (
      <Result
        status="info"
        title={t('暂无结果')}
        subTitle={t('文档处理完成（待审核）后方可在此查看。')}
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
  // Values matching the source and passing rules that nobody has confirmed yet ("Confirm all verified and
  // passing").
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

  // Why "Complete review" is unavailable right now.
  const finishBlocked = !can('document:review')
    ? t('你没有“完成审核”权限，请联系管理员开通')
    : pendingTotal > 0
      ? t('尚有 {{pendingTotal}} 个字段待确认', { pendingTotal })
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
          title={t('沿用上一轮的 {{suggestions}} 项审核结果？', { suggestions: doc.suggestions })}
          description={t('适用于上一轮经人工审核、且本轮值与位置均一致的字段：值相同的直接确认，上一轮修改过的改为修改后的值。')}
          okText={t('沿用')}
          cancelText={t('取消')}
          onConfirm={() => adopt.mutate()}
        >
          <Button icon={<HistoryOutlined />} loading={adopt.isPending}>
            {t('沿用上一轮审核（{{suggestions}}）', { suggestions: doc.suggestions })}
          </Button>
        </Popconfirm>
      )}
      {discover && canEdit && can('template:manage') && (
        <Button type="primary" ghost icon={<AppstoreAddOutlined />} onClick={() => setSavingTemplate(true)}>
          {t('保存为模板')}
        </Button>
      )}
      {!viewingOld && can('template:manage') && (
        <Tooltip title={t('在本单据原图上框选所需的值，创建新模板')}>
          <Button icon={<AppstoreAddOutlined />} onClick={() => navigate(`/templates/from-boxes/${doc.id}`)}>
            {t('框选创建模板')}
          </Button>
        </Tooltip>
      )}
      {!viewingOld && can('document:process') && <ReprocessButton doc={doc} />}
      {doc.status === 'done' && !viewingOld && !doc.skipped_at && can('document:review') && (
        <SkipButton doc={doc} next={() => goTo(neighbors.data?.next ?? neighbors.data?.previous)} />
      )}
      {/*
       * Field comments are recorded on the document's first page (visible from any page of a multi-page
       * document).
       */}
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
            {t('完成审核')}
          </Button>
        </Tooltip>
      )}
      {/* Always shown; greyed out when nothing is flagged, with a hover explanation. */}
      {canEdit && (
        <Popconfirm
          title={t('确认本单据 {{length}} 个标黄字段均正确？', { length: pagePending.length })}
          description={t('无需逐个确认。每个字段都会记录一条确认记录，可在修改历史中查看。')}
          okText={t('全部确认')}
          cancelText={t('取消')}
          disabled={pagePending.length === 0}
          onConfirm={() => confirmMany.mutate(pagePending)}
        >
          <Tooltip
            title={pagePending.length === 0 ? t('本单据已没有待确认的标黄字段') : undefined}
          >
            <Button
              icon={<CheckOutlined />}
              loading={confirmMany.isPending}
              disabled={pagePending.length === 0}
            >
              {t('全部确认（{{length}}）', { length: pagePending.length })}
            </Button>
          </Tooltip>
        </Popconfirm>
      )}
      {/*
       * Green values (matching the source and passing rules) not yet confirmed: confirm them at once, leaving
       * "reviewed by a person" in the history.
       */}
      {canEdit && (
        <Popconfirm
          title={t('确认本单据中 {{length}} 个与原文一致且规则检查通过的字段？', { length: pageVerified.length })}
          description={t('这些字段与原文完全一致且规则检查通过，确认后审核记录中将标记为人工已核对。')}
          okText={t('确认')}
          cancelText={t('取消')}
          disabled={pageVerified.length === 0}
          onConfirm={() => confirmMany.mutate(pageVerified)}
        >
          <Tooltip
            title={
              pageVerified.length === 0 ? t('本单据中与原文一致且规则通过的字段均已确认') : undefined
            }
          >
            <Button
              icon={<SafetyCertificateOutlined />}
              loading={confirmMany.isPending}
              disabled={pageVerified.length === 0}
            >
              {t('确认无问题字段（{{length}}）', { length: pageVerified.length })}
            </Button>
          </Tooltip>
        </Popconfirm>
      )}
      <ExportButton ids={[doc.id]} languages={languages} language={language} onLanguage={setLanguage} />
      {can('document:delete') && doc.status !== 'processing' && (
        <Popconfirm
          title={t('删除「{{filename}}」？', { filename: doc.filename })}
          description={t('原始文件、处理结果、审核记录和备注将全部删除，且无法恢复。删除后将打开下一份。')}
          okText={t('删除')}
          okButtonProps={{ danger: true }}
          cancelText={t('取消')}
          onConfirm={() => remove.mutate()}
        >
          <Button danger icon={<DeleteOutlined />} loading={remove.isPending}>
            {t('删除')}
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
            <Tooltip title={t('上一份待审核文档')}>
              <Button
                icon={<LeftOutlined />}
                aria-label={t('上一份')}
                disabled={!neighbors.data?.previous}
                onClick={() => goTo(neighbors.data?.previous)}
              />
            </Tooltip>
            <Tooltip
              title={
                neighbors.data
                  ? t('下一份待审核文档（除本份外剩余 {{remaining}} 份）', { remaining: neighbors.data.remaining })
                  : t('下一份待审核文档')
              }
            >
              <Button
                icon={<RightOutlined />}
                aria-label={t('下一份')}
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
      // Many buttons: place them on a line below the title instead of crowding the file name.
      styles={{ body: { paddingTop: 16 } }}
    >
      <div style={{ marginBottom: 12 }}>{toolbar}</div>
      <SkippedNotice doc={doc} canUndo={can('document:review') && !viewingOld} />
      {!doneOrReviewed && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title={doc.status === 'failed' ? t('重新处理失败，以下为上一轮结果') : t('正在重新处理')}
          description={
            doc.status === 'failed'
              ? doc.error
              : t('{{stage}}{{progress}}。以下为上一轮结果，仅供查看；处理完成后将自动更新为新结果。', {
                  stage: doc.stage ?? t('排队中'),
                  progress: doc.progress !== null ? t('（{{progress}}%）', { progress: doc.progress }) : '',
                })
          }
        />
      )}
      {viewingOld && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          title={t('此为第 {{viewing_run}} 轮的历史结果，仅供查看', { viewing_run: doc.viewing_run })}
          action={
            <Button size="small" onClick={() => setRun(undefined)}>
              {t('返回当前结果')}
            </Button>
          }
        />
      )}
      {discover && !viewingOld && (
        <Tip
          id="review-discover"
          style={{ marginBottom: 12 }}
          title={t('本文档未使用模板，已抽取单据上的所有键值对')}
          description={t('核对所需字段及其值，然后点击“保存为模板”：选择字段并命名，同类单据此后可直接使用该模板。')}
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
              key={drawn.join(',')} // Box changed: clear the re-recognition result.
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
                applyBox(drawn, text, byModel ? t('由模型读取原图，已经人工核对') : '', transcription)
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
              title={t('已框选。请先在右侧选择要填写的字段')}
            />
          )}
          {checking !== null && selected && (
            <TranscriptionPanel
              key={checking} // Another source block: restart the input.
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
              title={t('已选 {{length}} 块原文：「{{join}}」', { length: picked.length, join: picked
                .map((i) => ocr.data?.lines[i]?.text ?? '')
                .join(' ') })}
              action={
                <Flex gap={8}>
                  <Button
                    size="small"
                    type="primary"
                    loading={revise.isPending}
                    onClick={() => applyPick(picked)}
                  >
                    {t('填入「{{displayLabel}}」（Enter）', { displayLabel: displayLabel(values, selected) })}
                  </Button>
                  <Button size="small" onClick={() => setPicked([])}>
                    {t('取消（Esc）')}
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
              setDrawn(null) // Dragging again: discard the previous box.
            }}
            onDraftDone={setDrawn}
            extra={
              <>
                {/*
                 * Previous and next item: same as ↑↓ (in line items, the same column in the previous or next
                 * row).
                 */}
                <Tooltip title={t('上一项（↑）')}>
                  <Button
                    size="small"
                    icon={<UpOutlined />}
                    aria-label={t('上一项')}
                    disabled={!neighbor(values, selectedId, 'ArrowUp')}
                    onClick={() => {
                      const prev = neighbor(values, selectedId, 'ArrowUp')
                      if (prev) select(prev)
                    }}
                  />
                </Tooltip>
                <Tooltip title={selected ? t('下一项（↓）') : t('从第一项开始（↓）')}>
                  <Button
                    size="small"
                    icon={<DownOutlined />}
                    aria-label={t('下一项')}
                    disabled={!neighbor(values, selectedId, 'ArrowDown')}
                    onClick={() => {
                      const next = neighbor(values, selectedId, 'ArrowDown')
                      if (next) select(next)
                    }}
                  />
                </Tooltip>
                {canEdit && (
                <Tooltip title={t('所选字段值正确（Enter）')}>
                  <Button
                    size="small"
                    type={selected?.pending_review ? 'primary' : 'default'}
                    icon={<CheckOutlined />}
                    disabled={!selected || selected.deleted}
                    loading={revise.isPending && revise.variables?.change.action === 'confirm'}
                    onClick={confirmSelected}
                  >
                    {t('确认')}
                  </Button>
                </Tooltip>
                )}
              </>
            }
          />
        </div>
        {/*
         * The right side has two parts: details of the selected field on top (pinned, no scrolling) and
         * fields and line items below (scrolling independently).
         */}
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
                  // Open region mode starting from this field's current box: re-recognize directly, or drag
                  // it to the right place first.
                  const box = (selected.current_evidence ?? selected.evidence)?.bbox ?? null
                  setPicked([])
                  setDrawOn(true)
                  setDraft(box)
                  setDrawn(box)
                  if (!box) {
                    void message.info(t('在左侧原图上拖动框选区域'))
                  }
                }}
              />
            </div>
          )}
          {languages.length > 1 && (
            <Flex align="center" gap={8} style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary">{t('字段名')}</Typography.Text>
              <Select
                data-keep-selection
                size="small"
                aria-label={t('字段名的语言')}
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
                  title: t('删除第 {{display}} 行', { display: row.display }),
                  description:
                    t('此行将显示为灰色删除线，不计入明细合计，也不会导出；可随时恢复。'),
                  placeholder: t('例如：此为会员号，不是商品'),
                  okText: t('删除'),
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
                      { onSuccess: () => select(null) }, // The selected cell may be in the deleted row.
                    ),
                })
              }
              onRestoreRow={(table, row) =>
                setReasonRequest({
                  title: t('恢复此行'),
                  description: t('恢复为删除前的值。'),
                  placeholder: t('例如：误删'),
                  okText: t('恢复'),
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
            <Empty description={t('此页无抽取结果')} />
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


/** Document (extraction) containing page page. */
function coveringExtraction(extractions: Extraction[], page: number): Extraction | undefined {
  return extractions.find((e) => e.page <= page && page <= (e.last_page ?? e.page))
}

/**
 * Pagination: when a file contains several documents, the upper bar lists documents ("Document 2 · pages
 * 3–4"); when the selected document has several pages, the lower bar lists its pages. Nothing is shown for a
 * single one-page document.
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
  const { t } = useTranslation()
  const parts = doc.extractions
  const current = coveringExtraction(parts, page)
  const range = (e: Extraction) =>
    (e.last_page ?? e.page) === e.page ? t('第 {{page}} 页', { page: e.page }) : t('第 {{page}}–{{last_page}} 页', { page: e.page, last_page: e.last_page })
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
              label: t('单据 {{n}} · {{range}}{{pending}}', {
                n: i + 1,
                range: range(e),
                pending: pending ? t('（{{pending}} 待确认）', { pending }) : '',
              }),
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
            label: t('第 {{value}} 页', { value: current.page + i }),
            value: current.page + i,
          }))}
        />
      )}
      {!current && doc.page_count > 1 && (
        <Segmented
          value={page}
          onChange={(value) => onChange(Number(value))}
          options={Array.from({ length: doc.page_count }, (_, i) => ({
            label: t('第 {{value}} 页', { value: i + 1 }),
            value: i + 1,
          }))}
        />
      )}
    </Flex>
  )
}
