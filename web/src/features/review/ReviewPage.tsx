import {
  ArrowLeftOutlined,
  CheckOutlined,
  DownOutlined,
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
  Popconfirm,
  Result,
  Segmented,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { completeReview, getDocument, getPageOcr } from '../../api/documents'
import type { Box, FieldValue, RevisionIn } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { ExportButton } from '../documents/ExportButton'
import { StatusTag } from '../documents/StatusTag'
import { FieldDetail, FieldPanel } from './FieldPanel'
import { displayLabel, neighbor, splitCell } from './fields'
import { PageViewer } from './PageViewer'
import { ReasonDialog, type ReasonRequest } from './ReasonDialog'
import { RegionPanel } from './RegionPanel'
import { useConfirmMany } from './useConfirmMany'
import { useRevise } from './useRevise'
import { useRows } from './useRows'

/** 下一个还在等确认的字段（从 afterId 后面开始找，找到最后再从头找）。 */
function nextPending(values: FieldValue[], afterId: string | null): string | null {
  const start = values.findIndex((v) => v.id === afterId)
  const ordered = [...values.slice(start + 1), ...values.slice(0, start + 1)]
  return ordered.find((v) => v.pending_review && v.id !== afterId)?.id ?? null
}

/** 在输入框里打字、或者焦点在按钮上（按 Enter 会点按钮）时，不当作快捷键。 */
function isTyping(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    target.closest('input, textarea, button, [contenteditable]') !== null
  )
}

/** 审核工作台：左边原图和出处框，右边字段。选中字段可以修改或确认，全部处理完再"完成审核"。 */
export function ReviewPage() {
  const { id = '' } = useParams()
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
  const [reasonRequest, setReasonRequest] = useState<ReasonRequest | null>(null)
  // 框选：模式开关、正在画的框、画好了等着用的框
  const [drawOn, setDrawOn] = useState(false)
  const [draft, setDraft] = useState<Box | null>(null)
  const [drawn, setDrawn] = useState<Box | null>(null)

  const document = useQuery({ queryKey: ['document', id], queryFn: () => getDocument(id) })
  const ready = document.data?.status === 'done' || document.data?.status === 'reviewed'
  const ocr = useQuery({
    queryKey: ['page-ocr', id, page],
    queryFn: () => getPageOcr(id, page),
    enabled: ready,
    staleTime: Infinity, // OCR 结果处理完就不会变
  })
  const revise = useRevise(id)
  const rows = useRows(id)
  const confirmMany = useConfirmMany(id)
  const finish = useMutation({
    mutationFn: () => completeReview(id),
    onSuccess: () => {
      void message.success('已完成审核')
      void queryClient.invalidateQueries({ queryKey: ['document', id] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })

  const canEdit = can('value:review')
  const currentExtraction = document.data?.extractions.find((e) => e.page === page)
  // useMemo：数据没变就还是同一个数组，下面的键盘监听不用每次都重新绑
  const values = useMemo(() => currentExtraction?.values ?? [], [currentExtraction])
  const showOcr = (ocrOn || altHeld) && canEdit && ocr.data?.available === true
  const selected = values.find((v) => v.id === selectedId) ?? null

  /** 用这几块原文填进选中的字段。表格的格子填完跳到下一格（一行商品连着点下去）。 */
  function applyPick(lines: number[]) {
    if (lines.length > 0) {
      applyChange({ action: 'edit', reason: '', source_lines: lines })
    }
  }

  /** 用框选、重新识别并确认过的文字填进选中的字段。 */
  function applyBox(box: Box, text: string) {
    applyChange({ action: 'edit', reason: '', value: text, source_box: box })
  }

  function applyChange(change: RevisionIn) {
    if (!selected) {
      return
    }
    revise.mutate(
      { valueId: selected.id, change },
      {
        onSuccess: () => {
          setPicked([])
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
    setDraft(null)
    setDrawn(null)
    setSelectedId(valueId)
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
      setPicked((p) => (p.includes(index) ? p.filter((i) => i !== index) : [...p, index]))
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
  }, [values, selectedId, canEdit, revise, picked, reasonRequest, drawn, draft])

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
  if (doc.status !== 'done' && doc.status !== 'reviewed') {
    return (
      <Result
        status="info"
        title="还没有结果"
        subTitle="文档处理完（待审核）之后才能在这里看。"
        extra={back}
      />
    )
  }

  const extraction = doc.extractions.find((e) => e.page === page)
  const pendingTotal = doc.extractions.reduce(
    (sum, e) => sum + e.values.filter((v) => v.pending_review).length,
    0,
  )
  const changePage = (next: number) => {
    setPage(next)
    setSelectedId(null)
    setPicked([])
  }

  const pagePending = values.filter((v) => v.pending_review).map((v) => v.id)

  // "完成审核"按钮：为什么现在不能点
  const finishBlocked = !can('document:review')
    ? '你没有"完成审核"的权限，请找管理员开通'
    : pendingTotal > 0
      ? `还有 ${pendingTotal} 个字段等待确认`
      : null

  return (
    <Card
      title={
        <Flex align="center" gap={12}>
          <Typography.Text strong ellipsis style={{ maxWidth: 360 }}>
            {doc.filename}
          </Typography.Text>
          <StatusTag status={doc.status} />
        </Flex>
      }
      extra={
        <Flex gap={8}>
          {doc.status === 'done' && (
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
              title={`这一页 ${pagePending.length} 个标黄的都是对的？`}
              description="不用一个一个点。每个都会记一条确认记录，可以在修改历史里看到。"
              okText="全部确认"
              cancelText="取消"
              disabled={pagePending.length === 0}
              onConfirm={() => confirmMany.mutate(pagePending)}
            >
              <Tooltip
                title={pagePending.length === 0 ? '这一页没有标黄（等你确认）的了' : undefined}
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
          <ExportButton ids={[doc.id]} />
          {back}
        </Flex>
      }
      styles={{ body: { paddingTop: 16 } }}
    >
      {doc.page_count > 1 && (
        <Segmented
          value={page}
          onChange={(value) => changePage(Number(value))}
          options={Array.from({ length: doc.page_count }, (_, i) => ({
            label: `第 ${i + 1} 页`,
            value: i + 1,
          }))}
          style={{ marginBottom: 16 }}
        />
      )}
      <Flex gap={24} align="stretch" style={{ height: 'calc(100vh - 230px)', minHeight: 480 }}>
        <div style={{ flex: 1, minWidth: 0 }} data-keep-selection>
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
              onUseText={(text) => applyBox(drawn, text)}
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
            values={values}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={select}
            onHover={setHoveredId}
            ocr={ocr.data}
            showOcr={showOcr}
            onToggleOcr={() => setOcrOn((on) => !on)}
            picked={picked}
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
        <Flex vertical gap={8} style={{ width: 440, flexShrink: 0, minHeight: 0 }}>
          {selected && (
            <div
              data-keep-selection
              style={{ maxHeight: '55%', overflow: 'auto', flexShrink: 0, paddingRight: 4 }}
            >
              <FieldDetail
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
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', paddingRight: 4 }}>
          {extraction ? (
            <FieldPanel
              extraction={extraction}
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
    </Card>
  )
}
