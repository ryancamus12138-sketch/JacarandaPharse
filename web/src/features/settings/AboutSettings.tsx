import { CheckCircleFilled, CloseCircleFilled, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Collapse, Descriptions, Flex, List, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { useAuth } from '../../auth/useAuth'

const VISION_MODE: Record<string, string> = {
  auto: '照片、扫描件 OCR 不可靠时才看',
  always: '每张都看',
  off: '关掉了',
}

/**
 * 设置 → 关于和诊断（ADR 0063）：版本、各项服务通不通。
 * 模型服务地址、文件名这些细节默认折起来，而且只有管理员看得到（后端也不给别人返回）。
 */
export function AboutSettings() {
  const { can } = useAuth()
  const info = useQuery({ queryKey: ['system'], queryFn: getSystemInfo })
  const d = info.data
  const admin = can('system:manage')
  return (
    <Flex vertical gap={16}>
      {info.error && (
        <Alert type="error" showIcon title={info.error instanceof ApiError ? info.error.message : String(info.error)} />
      )}
      <Card title="关于" size="small" loading={info.isPending}>
        {d && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="JacarandaParse">v{d.version}</Descriptions.Item>
            <Descriptions.Item label="版本">{d.mode === 'team' ? '团队版' : '个人版（只在本机用，不用登录）'}</Descriptions.Item>
            <Descriptions.Item label="正在用的大模型">
              {d.model.loaded ?? <Typography.Text type="secondary">连不上模型服务</Typography.Text>}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      <Card
        title="服务状态"
        size="small"
        extra={
          <Button icon={<ReloadOutlined />} loading={info.isFetching} onClick={() => void info.refetch()}>
            重新检查
          </Button>
        }
        loading={info.isPending}
      >
        <List
          dataSource={d?.services ?? []}
          renderItem={(s) => (
            <List.Item>
              <List.Item.Meta
                avatar={
                  s.ok ? (
                    <CheckCircleFilled style={{ color: '#52c41a', fontSize: 18 }} />
                  ) : (
                    <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 18 }} />
                  )
                }
                title={s.name}
                description={
                  <>
                    {s.detail}
                    {s.hint && (
                      <div>
                        <Typography.Text type="secondary">怎么解决：{s.hint}</Typography.Text>
                      </div>
                    )}
                  </>
                }
              />
            </List.Item>
          )}
        />
      </Card>
      {admin && d && (
        <Collapse
          items={[
            {
              key: 'details',
              label: '诊断细节（排查问题时看）',
              children: (
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="模型服务">{d.model.server}</Descriptions.Item>
                  <Descriptions.Item label="上下文长度">
                    {d.model.context ? `${d.model.context.toLocaleString()} token` : '–'}
                  </Descriptions.Item>
                  <Descriptions.Item label="大模型文件">{d.model.file ?? '–'}</Descriptions.Item>
                  <Descriptions.Item label="看原图（视觉模型）">
                    {d.model.sees_images
                      ? `能看图（${d.model.vision_file ?? ''}）· ${VISION_MODE[d.model.vision_mode ?? 'auto'] ?? d.model.vision_mode}`
                      : d.model.vision_file
                        ? `放好了 ${d.model.vision_file}，重新 jacara start 后生效`
                        : '没有视觉模型'}
                  </Descriptions.Item>
                  <Descriptions.Item label="OCR">
                    {d.ocr.engine}
                    {d.ocr.version ? ` ${d.ocr.version}` : ''}
                  </Descriptions.Item>
                  <Descriptions.Item label="版面分析">{d.structure?.layout ?? '关闭'}</Descriptions.Item>
                  <Descriptions.Item label="表格结构">{d.structure?.table ?? '没有表格模型，按文字位置划分'}</Descriptions.Item>
                  <Descriptions.Item label="监视文件夹">{d.watch_dir ?? '没有设'}</Descriptions.Item>
                </Descriptions>
              ),
            },
          ]}
        />
      )}
    </Flex>
  )
}
