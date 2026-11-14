import { useQuery } from '@tanstack/react-query'
import { Flex, Select, Typography } from 'antd'
import { Link } from 'react-router'
import { listTemplates } from '../../api/templates'
import { AUTO, DISCOVER, templateOptions } from './uploadChoice'
import { Tip } from '../../components/Tip'
import { useTranslation } from 'react-i18next'

/**
 * Template selection before upload. On first use (no templates of one's own yet), suggests adding one from
 * the library or choosing "extract all key-value pairs".
 * An empty value uses the template of the previous upload (decided by the backend).
 */
export function TemplatePicker({
  value,
  onChange,
}: {
  value: string | undefined
  onChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const templates = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const mine = (templates.data ?? []).filter((tpl) => tpl.kind === 'mine' && !tpl.archived)
  // The remembered template was archived or deleted: treat as unselected.
  const known =
    value === DISCOVER || (value === AUTO && mine.length > 0) || mine.some((tpl) => tpl.id === value)
  return (
    <Flex vertical gap={8} style={{ marginBottom: 12 }}>
      {templates.isSuccess && mine.length === 0 && (
        <Tip
          id="upload-no-template"
          title={t('还没有自己的模板')}
          description={
            <>
              {t('去')}{' '}<Link to="/templates">{t('模板')}</Link>{' '}{t('页，从起步模板库（数电发票、行程单、收据、酒店账单……） 添加一个；或者先选"抽取所有键值对"，处理完在审核页面把需要的字段存成模板。 没有自己的模板时，上传的文件会抽出单据上所有的键值对。')}
            </>
          }
        />
      )}
      <Flex align="center" gap={8} wrap>
        <Typography.Text>{t('用哪个模板：')}</Typography.Text>
        <Select
          style={{ minWidth: 320 }}
          placeholder={t('和上次一样')}
          loading={templates.isPending}
          value={known ? value : undefined}
          onChange={onChange}
          options={templateOptions(templates.data ?? [])}
          showSearch={{ optionFilterProp: 'search' }}
        />
      </Flex>
      {value === AUTO && known && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('每张单据先按字段名、模板名找出最像的几个模板，再让模型选一个；都不合适就抽出所有键值对。 选得不对，在审核页"重新处理"里换模板。')}
        </Typography.Text>
      )}
    </Flex>
  )
}
