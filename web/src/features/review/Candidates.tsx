import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Button, Flex, Image, Tag, Tooltip, Typography } from 'antd'
import { getPageCrop } from '../../api/documents'
import type { Candidate, FieldValue } from '../../api/types'
import type { Revise } from './useRevise'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

/** Source of a candidate. */
function sourceName(c: Candidate): string {
  switch (c.source) {
    case 'ocr':
      return 'OCR'
    case 'text_layer':
      return t('PDF 文字层')
    case 'zoom':
      return c.dpi ? t('放大到 {{dpi}} DPI 再识别', { dpi: c.dpi }) : t('原图局部再识别')
    case 'form':
      return t('表单字段')
  }
}

const SOURCE_COLOR: Record<Candidate['source'], string> = {
  ocr: 'blue',
  text_layer: 'purple',
  zoom: 'cyan',
  form: 'geekblue',
}

/**
 * Zoomed image of the candidate's region (PDFs re-rendered at 360 DPI, images from the original), fetched
 * with the token like page images.
 */
function Crop({ documentId, c }: { documentId: string; c: Candidate }) {
  const { t } = useTranslation()
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
      alt={t('原图这一块')}
      style={{ maxHeight: 48, maxWidth: 220, objectFit: 'contain', border: '1px solid #eee' }}
      preview={{ mask: t('放大看') }}
    />
  )
}

/**
 * Differing values from different sources: full-page OCR, PDF text layer, zoomed re-recognition, form fields.
 * Never applied automatically; a reviewer compares them with the zoomed image and accepts one (recorded in
 * the history).
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
  const { t } = useTranslation()
  const items = v.candidates?.items ?? []
  if (items.length === 0) return null
  const busy = revise.isPending && revise.variables?.valueId === v.id
  return (
    <div style={{ marginTop: 8 }}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('几个来源读出来的不一样，看图选一个：')}
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
                <Tooltip title={t('换成这个值，和这个字段有关的规则都能通过（只是提示）')}>
                  <Tag icon={<CheckOutlined />} color="success" style={{ marginInlineEnd: 0 }}>
                    {t('符合规则')}
                  </Tag>
                </Tooltip>
              )}
              {c.rule_ok === false && (
                <Tooltip title={t('换成这个值，和这个字段有关的规则过不了（只是提示）')}>
                  <Tag icon={<CloseOutlined />} color="warning" style={{ marginInlineEnd: 0 }}>
                    {t('规则不通过')}
                  </Tag>
                </Tooltip>
              )}
              {c.source === 'form' && c.note && (
                <Tag color={c.visible === true ? 'default' : 'orange'} style={{ marginInlineEnd: 0 }}>
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
                          reason: t('看原图后采用{{sourceName}}读的「{{text}}」', { sourceName: sourceName(c), text: c.text }),
                        },
                      },
                      { onSuccess: onDone },
                    )
                  }
                >
                  {t('采用')}
                </Button>
              )}
              {current && <Tag style={{ marginInlineEnd: 0 }}>{t('现在的值')}</Tag>}
            </Flex>
          )
        })}
      </Flex>
    </div>
  )
}
