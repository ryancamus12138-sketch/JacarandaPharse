import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Button, Flex, Input, Popover, Select, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'
import { ApiError } from '../../api/client'
import { type Correction, type Evidence, type Stage, type StageCount, overrideAttribution } from '../../api/insights'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'

/** Display name and color of each stage. */
const STAGES: Record<Stage, { name: string; color: string; tip: string }> = {
  ocr_misread: { get name() { return t('OCR 识别错误') }, color: 'magenta', get tip() { return t('OCR 误识别原图文字：可更换 OCR 模型或收集纠错样本') } },
  ocr_uncovered: { get name() { return t('OCR 漏识别') }, color: 'volcano', get tip() { return t('OCR 未识别出此区域的任何文字：如手写、印章或字迹过淡') } },
  extraction: { get name() { return t('模型抽取错误或遗漏') }, color: 'blue', get tip() { return t('值已包含在发送给模型的文字中：可修改字段说明、别名，或更换模型') } },
  matching: { get name() { return t('标准化或出处匹配问题') }, color: 'cyan', get tip() { return t('模型输出正确，但在出处核对时被丢弃或在格式转换时出错') } },
  template: { get name() { return t('模板或规则') }, color: 'purple', get tip() { return t('模板设置（如日月顺序）导致值出错') } },
  mixed: { get name() { return t('多个环节有问题') }, color: 'gold', get tip() { return t('多个环节均有证据，展开查看') } },
  unknown: { get name() { return t('证据不足') }, color: 'default', get tip() { return t('证据不足，暂不下结论') } },
}

const STAGE_OPTIONS = (Object.keys(STAGES) as Stage[])
  .filter((s) => s !== 'mixed' && s !== 'unknown')
  .map((s) => ({ value: s, label: STAGES[s].name }))

/** Summary of stages at fault: count per stage. */
export function StageSummary({ stages }: { stages: StageCount[] }) {
  if (stages.length === 0) return <Typography.Text type="secondary">—</Typography.Text>
  return (
    <Flex gap={6} wrap>
      {stages.map((s) => (
        <Tooltip key={s.stage} title={STAGES[s.stage]?.tip}>
          <Tag color={STAGES[s.stage]?.color} style={{ marginInlineEnd: 0 }}>
            {s.name} {s.count}
          </Tag>
        </Tooltip>
      ))}
    </Flex>
  )
}

function EvidenceLine({ e }: { e: Evidence }) {
  const { t } = useTranslation()
  const prefix = e.stage ? t('{{value}}：', { value: STAGES[e.stage]?.name ?? e.stage }) : ''
  let text = ''
  if (e.kind === 'ocr_sample') text = t('OCR 样本：OCR 识别为“{{value}}”，人工转写为“{{value2}}”', { value: e.ocr_text ?? '', value2: e.transcription ?? '' })
  else if (e.kind === 'sent_text')
    text = t(e.source === 'sent' ? '发送给模型的文字中包含：{{lines}}' : '当时的 OCR 文字（更新前处理，未记录发送给模型的内容）中包含：{{lines}}', {
      lines: (e.lines ?? []).join(t(' ／ ')) || t('（跨多行）'),
    })
  else if (e.kind === 'revision')
    text = e.drawn ? t('人工框选区域，OCR 当时识别为：“{{value}}”', { value: e.ocr_text || t('（无内容）') }) : t('人工点选的原文，OCR 识别为：“{{value}}”', { value: e.ocr_text ?? '' })
  else if (e.kind === 'value') text = e.rejected_value != null ? t('模型输出为 {{rejected_value}}，出处核对未找到，已被丢弃', { rejected_value: e.rejected_value }) : t('模型输出为 {{value}}', { value: e.value ?? '' })
  else if (e.kind === 'template') text = t('模板设定的日期顺序：{{order}}', { order: e.day_first ? t('日在前') : t('月在前') })
  return (
    <Typography.Text style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
      {prefix}
      {text}
    </Typography.Text>
  )
}

/** Stage at fault for one correction: expand to see reason and evidence, and override if needed. */
export function StageCell({ c, canEdit }: { c: Correction; canEdit: boolean }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [stage, setStage] = useState<Stage | undefined>(c.overridden ? (c.stage ?? undefined) : undefined)
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: (next: Stage | null) => overrideAttribution(c.attribution_id ?? '', next, note),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['insights'] })
      void message.success(t('已修改'))
    },
    onError: (err) => void message.error(err instanceof ApiError ? err.message : String(err)),
  })
  if (!c.stage || !c.attribution_id) return <Typography.Text type="secondary">—</Typography.Text>
  const info = STAGES[c.stage]
  const content = (
    <Flex vertical gap={8} style={{ width: 380, maxWidth: '80vw' }}>
      <Typography.Text>{c.stage_reason}</Typography.Text>
      {c.overridden && c.auto_stage && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('自动判断为：{{name}}', { name: STAGES[c.auto_stage]?.name })}
        </Typography.Text>
      )}
      {c.evidence.map((e, i) => (
        <EvidenceLine key={i} e={e} />
      ))}
      {canEdit && (
        <Flex vertical gap={6} style={{ borderTop: '1px solid rgba(0,0,0,0.06)', paddingTop: 8 }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('判断有误，改为：')}
          </Typography.Text>
          <Select size="small" placeholder={t('出错环节')} value={stage} onChange={setStage} options={STAGE_OPTIONS} />
          <Input size="small" placeholder={t('原因（选填）')} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          <Flex gap={8} justify="flex-end">
            {c.overridden && (
              <Button size="small" loading={save.isPending} onClick={() => save.mutate(null)}>
                {t('撤销改判')}
              </Button>
            )}
            <Button size="small" type="primary" disabled={!stage} loading={save.isPending} onClick={() => save.mutate(stage ?? null)}>
              {t('改判')}
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  )
  return (
    <Popover title={t('出错环节')} content={content} trigger="click">
      <Tag color={info?.color} style={{ cursor: 'pointer' }}>
        {c.stage_name}
        {c.overridden ? t('（人工改判）') : ''}
      </Tag>
    </Popover>
  )
}
