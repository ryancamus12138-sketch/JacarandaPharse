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
import { useTranslation } from 'react-i18next'

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err))

/** After skipping, unskipping or commenting, the list, dashboard and previous / next must be refetched. */
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
 * "Skip for now": set aside a document that needs more thought (optionally with a reason) and open the next
 * one.
 * Skipped documents are excluded from "Next"; the document list can filter to skipped documents for later.
 */
export function SkipButton({ doc, next }: { doc: DocumentDetail; next: () => void }) {
  const { t } = useTranslation()
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
      void message.success(t('已跳过。可在文档列表中选择“仅显示已跳过”找回'))
      next()
    },
    onError: (err) => void message.error(errorText(err)),
  })
  return (
    <>
      <Button icon={<FastForwardOutlined />} onClick={() => setOpen(true)}>
        {t('跳过')}
      </Button>
      <Modal
        title={t('跳过此文档？')}
        open={open}
        okText={t('跳过并打开下一份')}
        cancelText={t('取消')}
        confirmLoading={skip.isPending}
        onOk={() => skip.mutate()}
        onCancel={() => setOpen(false)}
      >
        <Typography.Paragraph type="secondary">
          {t('已跳过的文档不计入“下一份”。完成审核、重新处理或点击“取消跳过”后，将重新回到待审核列表。')}
        </Typography.Paragraph>
        <Input.TextArea
          value={reason}
          maxLength={500}
          autoSize={{ minRows: 2, maxRows: 5 }}
          placeholder={t('跳过原因（选填），例如：字迹不清，需向财务确认')}
          onChange={(e) => setReason(e.target.value)}
        />
      </Modal>
    </>
  )
}

/** Notice at the top of a skipped document, with "Unskip". */
export function SkippedNotice({ doc, canUndo }: { doc: DocumentDetail; canUndo: boolean }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const refresh = useRefresh(doc.id)
  const unskip = useMutation({
    mutationFn: () => unskipDocument(doc.id),
    onSuccess: () => {
      refresh()
      void message.success(t('已取消跳过，已返回待审核列表'))
    },
    onError: (err) => void message.error(errorText(err)),
  })
  if (!doc.skipped_at) return null
  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginBottom: 12 }}
      title={t('此文档于 {{formatTime}} 被跳过', { formatTime: formatTime(doc.skipped_at) })}
      description={doc.skip_reason ? t('原因：{{skip_reason}}', { skip_reason: doc.skip_reason }) : undefined}
      action={
        canUndo && (
          <Button size="small" icon={<UndoOutlined />} loading={unskip.isPending} onClick={() => unskip.mutate()}>
            {t('取消跳过')}
          </Button>
        )
      }
    />
  )
}

/**
 * Comments: notes for colleagues, on the whole document or on the field selected on the right.
 * Anyone who can view the document can write and resolve comments; only the author or an administrator can
 * delete them.
 */
export function CommentsButton({
  doc,
  selected,
  page,
}: {
  doc: DocumentDetail
  selected: FieldValue | null
  /** Current page (field comments record the page). */
  page: number
}) {
  const { t } = useTranslation()
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
          {t('备注')}
        </Button>
      </Badge>
      <Drawer title={t('备注')} open={open} onClose={() => setOpen(false)} size={420}>
        <Flex vertical gap={8} style={{ marginBottom: 16 }}>
          <Segmented<'document' | 'field'>
            value={onField ? 'field' : 'document'}
            onChange={setTarget}
            options={[
              { value: 'document', label: t('针对整份文档') },
              {
                value: 'field',
                label: selected ? t('针对「{{fieldLabel}}」', { fieldLabel: fieldLabel(selected.name) }) : t('请先在右侧选择字段'),
                disabled: selected === null,
              },
            ]}
          />
          <Input.TextArea
            value={body}
            maxLength={2000}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={t('给同事的备注，例如：需向财务确认')}
            onChange={(e) => setBody(e.target.value)}
          />
          <Button
            type="primary"
            disabled={!body.trim()}
            loading={add.isPending}
            onClick={() => add.mutate()}
            style={{ alignSelf: 'flex-end' }}
          >
            {t('添加')}
          </Button>
        </Flex>
        <List
          loading={comments.isPending}
          dataSource={comments.data ?? []}
          locale={{ emptyText: <Empty description={t('暂无备注')} /> }}
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
                  {c.resolved_at ? t('重新打开') : t('标为已解决')}
                </Button>,
                (c.mine || me?.role === 'admin') && (
                  <Popconfirm key="delete" title={t('删除此备注？')} okText={t('删除')} cancelText={t('取消')} onConfirm={() => remove.mutate(c.id)}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={t('删除')} />
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
                        {doc.page_count > 1 && c.page ? t('第 {{page}} 页 · ', { page: c.page }) : ''}
                        {fieldLabel(c.field_name)}
                      </Tag>
                    )}
                    {c.resolved_at && <Tag color="green">{t('{{resolved_by}} 已标记为已解决', { resolved_by: c.resolved_by })}</Tag>}
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
