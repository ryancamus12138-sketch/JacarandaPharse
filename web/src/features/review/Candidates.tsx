import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Button, Flex, Image, Tag, Tooltip, Typography } from 'antd'
import { getPageCrop } from '../../api/documents'
import type { Candidate, FieldValue } from '../../api/types'
import type { Revise } from './useRevise'

/** 候选从哪来（ADR 0042）。 */
function sourceName(c: Candidate): string {
  switch (c.source) {
    case 'ocr':
      return 'OCR'
    case 'text_layer':
      return 'PDF 文字层'
    case 'zoom':
      return c.dpi ? `放大到 ${c.dpi} DPI 再识别` : '原图局部再识别'
    case 'form':
      return '表单字段'
  }
}

const SOURCE_COLOR: Record<Candidate['source'], string> = {
  ocr: 'blue',
  text_layer: 'purple',
  zoom: 'cyan',
  form: 'geekblue',
}

/** 候选所在那一块放大后的图（PDF 按 360 DPI 重新画，图片用原图）。和页面图片一样先带 token 取回来。 */
function Crop({ documentId, c }: { documentId: string; c: Candidate }) {
  const box = c.box
  const crop = useQuery({
    queryKey: ['crop', documentId, c.page, box?.join(',')],
    queryFn: async () => URL.createObjectURL(await getPageCrop(documentId, c.page, box ?? [0, 0, 0, 0])),
    enabled: box !== null,
    staleTime: Infinity,
  })
  if (!box || !crop.data) return null
  return (
    <Image
      src={crop.data}
      alt="原图这一块"
      style={{ maxHeight: 48, maxWidth: 220, objectFit: 'contain', border: '1px solid #eee' }}
      preview={{ mask: '放大看' }}
    />
  )
}

/**
 * 各个来源读出来的不同的值（ADR 0042）：整页 OCR、PDF 文字层、局部放大再识别、表单字段。
 * 系统不会自己替换，人看着局部图选一个"采用"（会记进修改记录）。
 */
export function Candidates({
  documentId,
  value: v,
  revise,
  canEdit,
  onDone,
}: {
  documentId: string
  value: FieldValue
  revise: Revise
  canEdit: boolean
  onDone: () => void
}) {
  const items = v.candidates?.items ?? []
  if (items.length === 0) return null
  const busy = revise.isPending && revise.variables?.valueId === v.id
  return (
    <div style={{ marginTop: 8 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        几个来源读出来的不一样，看图选一个：
      </Typography.Text>
      <Flex vertical gap={6} style={{ marginTop: 4 }}>
        {items.map((c, i) => {
          const shown = c.value ?? c.text
          const current = v.current_value !== null && shown === v.current_value
          return (
            <Flex
              key={i}
              align="center"
              gap={8}
              wrap
              style={{ padding: '4px 6px', background: '#fff', borderRadius: 6, border: '1px solid #eee' }}
            >
              <Tag color={SOURCE_COLOR[c.source]} style={{ marginInlineEnd: 0 }}>
                {sourceName(c)}
              </Tag>
              <Typography.Text strong style={{ overflowWrap: 'anywhere' }}>
                {c.text}
              </Typography.Text>
              {c.value !== null && c.value !== c.text && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  → {c.value}
                </Typography.Text>
              )}
              {c.rule_ok === true && (
                <Tooltip title="换成这个值，和这个字段有关的规则都能通过（只是提示）">
                  <Tag icon={<CheckOutlined />} color="success" style={{ marginInlineEnd: 0 }}>
                    符合规则
                  </Tag>
                </Tooltip>
              )}
              {c.rule_ok === false && (
                <Tooltip title="换成这个值，和这个字段有关的规则过不了（只是提示）">
                  <Tag icon={<CloseOutlined />} color="warning" style={{ marginInlineEnd: 0 }}>
                    规则不通过
                  </Tag>
                </Tooltip>
              )}
              {c.source === 'form' && c.note && (
                <Tag color={c.note === '页面上能看到' ? 'default' : 'orange'} style={{ marginInlineEnd: 0 }}>
                  {c.note}
                </Tag>
              )}
              <Crop documentId={documentId} c={c} />
              {canEdit && !current && (
                <Button
                  size="small"
                  loading={busy}
                  onClick={() =>
                    revise.mutate(
                      {
                        valueId: v.id,
                        change: {
                          action: 'edit',
                          value: shown,
                          reason: `看原图后采用${sourceName(c)}读的「${c.text}」`,
                        },
                      },
                      { onSuccess: onDone },
                    )
                  }
                >
                  采用
                </Button>
              )}
              {current && <Tag style={{ marginInlineEnd: 0 }}>现在的值</Tag>}
            </Flex>
          )
        })}
      </Flex>
    </div>
  )
}
