import { DownOutlined, ExclamationCircleOutlined, UpOutlined } from '@ant-design/icons'
import { Alert, Button, Flex } from 'antd'
import { useState } from 'react'
import type { Checks } from '../../api/types'

const KEY = 'jacara.review.issues-open'

function remembered(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

/**
 * 需要核对的地方（规则没通过：超出参考范围、可能重复报销……）：平时收成一个感叹号按钮，写着几处；
 * 点开看每一处，再点收起。
 * 开着还是收着记在这个浏览器里。有"错误"级别的按钮是红的，只有"警告"是黄的。
 */
export function IssueToggle({ checks }: { checks: Checks }) {
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
      // 存不了就算了
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
        {issues.length} 处需要核对{errors > 0 ? `（${errors} 处有错）` : ''}
        {open ? <UpOutlined /> : <DownOutlined />}
      </Button>
      {open &&
        issues.map((issue, i) => (
          <Alert
            // 同一条规则可能报好几次（明细每一行单独检查）
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
