import { ArrowLeftOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Card,
  Empty,
  Flex,
  Result,
  Segmented,
  Spin,
  Tooltip,
  Typography,
} from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ApiError } from '../../api/client'
import { completeReview, getDocument } from '../../api/documents'
import type { FieldValue } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { StatusTag } from '../documents/StatusTag'
import { FieldPanel } from './FieldPanel'
import { PageViewer } from './PageViewer'
import { useRevise } from './useRevise'

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

  const document = useQuery({ queryKey: ['document', id], queryFn: () => getDocument(id) })
  const revise = useRevise(id)
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
  // useMemo：数据没变就还是同一个数组，下面的键盘监听不用每次都重新绑
  const values = useMemo(
    () => document.data?.extractions.find((e) => e.page === page)?.values ?? [],
    [document.data, page],
  )

  // 键盘：↑↓ 切换字段，Enter 确认选中的字段，Esc 取消选中。审核员一天看几百张，鼠标太慢
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target) || values.length === 0) {
        return
      }
      const index = values.findIndex((v) => v.id === selectedId)
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const step = e.key === 'ArrowDown' ? 1 : -1
        const next = index === -1 ? 0 : (index + step + values.length) % values.length
        setSelectedId(values[next].id)
      } else if (e.key === 'Escape') {
        setSelectedId(null)
      } else if (e.key === 'Enter' && canEdit && index !== -1 && !revise.isPending) {
        e.preventDefault()
        const current = values[index]
        revise.mutate(
          { valueId: current.id, change: { action: 'confirm', reason: '' } },
          { onSuccess: () => setSelectedId(nextPending(values, current.id)) },
        )
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [values, selectedId, canEdit, revise])

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
  }

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
        <div style={{ flex: 1, minWidth: 0 }}>
          <PageViewer
            documentId={doc.id}
            page={page}
            values={values}
            selectedId={selectedId}
            hoveredId={hoveredId}
            onSelect={setSelectedId}
            onHover={setHoveredId}
          />
        </div>
        <div style={{ width: 440, flexShrink: 0, overflow: 'auto', paddingRight: 4 }}>
          {extraction ? (
            <FieldPanel
              extraction={extraction}
              selectedId={selectedId}
              hoveredId={hoveredId}
              onSelect={setSelectedId}
              onHover={setHoveredId}
              canEdit={canEdit}
              revise={revise}
              onReviewed={(valueId) => setSelectedId(nextPending(values, valueId))}
            />
          ) : (
            <Empty description="这一页没有抽取结果" />
          )}
        </div>
      </Flex>
    </Card>
  )
}
