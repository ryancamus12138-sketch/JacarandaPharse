import { DownOutlined, ExclamationCircleOutlined, UpOutlined } from '@ant-design/icons'
import { Alert, Button, Flex } from 'antd'
import { useState } from 'react'
import type { Checks } from '../../api/types'
import { useTranslation } from 'react-i18next'

const KEY = 'jacara.review.issues-open'

function remembered(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Items to check (failed rules: out of reference range, possible duplicate claim, …): normally collapsed into
 * an exclamation button showing the count; click to expand each item, click again to collapse.
 * The expanded state is remembered in this browser. The button is red when any error-level issue exists,
 * yellow for warnings only.
 */
export function IssueToggle({ checks }: { checks: Checks }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(remembered)
  const issues = checks.issues
  if (issues.length === 0) return null
  const errors = issues.filter((i) => i.severity === 'error').length

  function toggle() {
    const next = !open
    setOpen(next)
    try {
      localStorage.setItem(KEY, next ? '1' : '0')
    } catch {
      // Storage unavailable: ignore.
    }
  }

  return (
    <Flex vertical gap={8}>
      <Button
        size="small"
        danger={errors > 0}
        icon={<ExclamationCircleOutlined />}
        onClick={toggle}
        style={{
          alignSelf: 'flex-start',
          ...(errors > 0 ? {} : { color: '#d48806', borderColor: '#ffd666' }),
        }}
      >
        {t('{{length}} 处需要核对', { length: issues.length })}{errors > 0 ? t('（{{errors}} 处有错）', { errors }) : ''}
        {open ? <UpOutlined /> : <DownOutlined />}
      </Button>
      {open &&
        issues.map((issue, i) => (
          <Alert
            // One rule may report several times (line items are checked per row).
            key={`${issue.rule}:${i}`}
            type={issue.severity === 'error' ? 'error' : 'warning'}
            showIcon
            title={issue.title}
            description={issue.message}
          />
        ))}
    </Flex>
  )
}
