import {
  CheckOutlined,
  DeleteOutlined,
  FastForwardOutlined,
  MessageOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Badge,
  Button,
  Drawer,
  Empty,
  Flex,
  Input,
  List,
  Modal,
  Popconfirm,
  Segmented,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import {
  addComment,
  deleteComment,
  resolveComment,
  skipDocument,
  unskipDocument,
} from '../../api/documents'
import type { DocumentDetail, FieldValue } from '../../api/types'
import { useAuth } from '../../auth/useAuth'
import { formatTime } from '../../utils/format'
import { useComments } from './comments'
import { fieldLabel } from './fields'

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err))

/** 跳过、取消跳过、写备注之后，列表、工作台、上一份 / 下一份都要重新取。 */
function useRefresh(id: string) {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: ['document', id] })
    void client.invalidateQueries({ queryKey: ['documents'] })
    void client.invalidateQueries({ queryKey: ['dashboard'] })
    void client.invalidateQueries({ queryKey: ['neighbors'] })
  }
}

/**
 * "先跳过"：拿不准的先放一放（可以写个原因），自动打开下一份。
 * 跳过的不算进"下一份"；文档列表里可以筛"只看跳过的"，过后再回来。
 */
export function SkipButton({ doc, next }: { doc: DocumentDetail; next: () => void }) {
  const { message } = App.useApp()
  const refresh = useRefresh(doc.id)
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const skip = useMutation({
    mutationFn: () => skipDocument(doc.id, reason),
    onSuccess: () => {
      setOpen(false)
      setReason('')
      refresh()
      void message.success('先跳过了。文档列表里选"只看跳过的"可以找回来')
      next()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <>
      <Button icon={<FastForwardOutlined />} onClick={() => setOpen(true)}>
        先跳过
      </Button>
      <Modal
        title="先跳过这份？"
        open={open}
        okText="跳过，看下一份"
        cancelText="取消"
        confirmLoading={skip.isPending}
        onOk={() => skip.mutate()}
        onCancel={() => setOpen(false)}
      >
        <Typography.Paragraph type="secondary">
          跳过的不算进"下一份"。完成审核、重新处理或点"取消跳过"后回到待审核里。
        </Typography.Paragraph>
        <Input.TextArea
          value={reason}
          maxLength={500}
          autoSize={{ minRows: 2, maxRows: 5 }}
          placeholder="为什么跳过（可以不填），例如：看不清，要问财务"
          onChange={(e) => setReason(e.target.value)}
        />
      </Modal>
    </>
  )
}

/** 跳过的文档顶上的提示，带"取消跳过"。 */
export function SkippedNotice({ doc, canUndo }: { doc: DocumentDetail; canUndo: boolean }) {
  const { message } = App.useApp()
  const refresh = useRefresh(doc.id)
  const unskip = useMutation({
    mutationFn: () => unskipDocument(doc.id),
    onSuccess: () => {
      refresh()
      void message.success('已取消跳过，回到待审核里')
    },
    onError: (err) => void message.error(errorText(err)),
  })
  if (!doc.skipped_at) return null
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      title={`这份在 ${formatTime(doc.skipped_at)} 先跳过了`}
      description={doc.skip_reason ? `原因：${doc.skip_reason}` : undefined}
      action={
        canUndo && (
          <Button size="small" icon={<UndoOutlined />} loading={unskip.isPending} onClick={() => unskip.mutate()}>
            取消跳过
          </Button>
        )
      }
    />
  )
}

/**
 * 备注：留给同事的话。可以写在整份文档上，也可以写在右边选中的那个字段上。
 * 能看这份文档的人都能写、都能标"已解决"；只有写的人或管理员能删。
 */
export function CommentsButton({
  doc,
  selected,
  page,
}: {
  doc: DocumentDetail
  selected: FieldValue | null
  /** 现在看的是第几页（写在字段上的备注记下页码）。 */
  page: number
}) {
  const { message } = App.useApp()
  const { me } = useAuth()
  const client = useQueryClient()
  const refresh = useRefresh(doc.id)
  const [open, setOpen] = useState(false)
  const [body, setBody] = useState('')
  const [target, setTarget] = useState<'document' | 'field'>('document')
  const comments = useComments(doc.id)
  const reload = () => {
    void client.invalidateQueries({ queryKey: ['comments', doc.id] })
    refresh()
  }
  const onField = target === 'field' && selected !== null
  const add = useMutation({
    mutationFn: () =>
      addComment(doc.id, body.trim(), onField ? { name: selected.name, page } : undefined),
    onSuccess: () => {
      setBody('')
      reload()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  const resolve = useMutation({
    mutationFn: ({ id, resolved }: { id: string; resolved: boolean }) => resolveComment(id, resolved),
    onSuccess: reload,
    onError: (err) => void message.error(errorText(err)),
  })
  const remove = useMutation({
    mutationFn: deleteComment,
    onSuccess: reload,
    onError: (err) => void message.error(errorText(err)),
  })
  const open_ = (comments.data ?? []).filter((c) => c.resolved_at === null).length

  return (
    <>
      <Badge count={open_} size="small" offset={[-4, 4]}>
        <Button icon={<MessageOutlined />} onClick={() => setOpen(true)}>
          备注
        </Button>
      </Badge>
      <Drawer title="备注" open={open} onClose={() => setOpen(false)} size={420}>
        <Flex vertical gap={8} style={{ marginBottom: 16 }}>
          <Segmented<'document' | 'field'>
            value={onField ? 'field' : 'document'}
            onChange={setTarget}
            options={[
              { value: 'document', label: '写在整份文档上' },
              {
                value: 'field',
                label: selected ? `写在「${fieldLabel(selected.name)}」上` : '先在右边选一个字段',
                disabled: selected === null,
              },
            ]}
          />
          <Input.TextArea
            value={body}
            maxLength={2000}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder="留给同事的话，例如：这张要问财务"
            onChange={(e) => setBody(e.target.value)}
          />
          <Button
            type="primary"
            disabled={!body.trim()}
            loading={add.isPending}
            onClick={() => add.mutate()}
            style={{ alignSelf: 'flex-end' }}
          >
            写上
          </Button>
        </Flex>
        <List
          loading={comments.isPending}
          dataSource={comments.data ?? []}
          locale={{ emptyText: <Empty description="还没有备注" /> }}
          renderItem={(c) => (
            <List.Item
              style={{ opacity: c.resolved_at ? 0.55 : 1 }}
              actions={[
                <Button
                  key="resolve"
                  size="small"
                  type="text"
                  icon={c.resolved_at ? <UndoOutlined /> : <CheckOutlined />}
                  onClick={() => resolve.mutate({ id: c.id, resolved: c.resolved_at === null })}
                >
                  {c.resolved_at ? '重新打开' : '标为已解决'}
                </Button>,
                (c.mine || me?.role === 'admin') && (
                  <Popconfirm key="delete" title="删掉这条备注？" okText="删除" cancelText="取消" onConfirm={() => remove.mutate(c.id)}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label="删除" />
                  </Popconfirm>
                ),
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={
                  <Flex gap={6} wrap align="center">
                    <Typography.Text strong>{c.author}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {formatTime(c.created_at)}
                    </Typography.Text>
                    {c.field_name && (
                      <Tag>
                        {doc.page_count > 1 && c.page ? `第 ${c.page} 页 · ` : ''}
                        {fieldLabel(c.field_name)}
                      </Tag>
                    )}
                    {c.resolved_at && <Tag color="green">{c.resolved_by} 标了已解决</Tag>}
                  </Flex>
                }
                description={
                  <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{c.body}</Typography.Paragraph>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </>
  )
}
