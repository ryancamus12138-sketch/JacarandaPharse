import { CheckCircleFilled, CloseCircleFilled, ReloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Descriptions, Flex, List, Row, Tag, Typography } from 'antd'
import { ApiError } from '../../api/client'
import { getSystemInfo } from '../../api/system'
import { formatBytes } from '../../utils/format'

/**
 * 系统信息（只读）：版本、模型、OCR、模板、数据量，和各个服务连不连得上。
 * 出问题时先看这里；和终端里的 uv run jacara doctor 是同一套检查。
 */
export function SystemPage() {
  const info = useQuery({ queryKey: ['system'], queryFn: getSystemInfo })
  const d = info.data

  return (
    <Flex vertical gap={16}>
      {info.error && (
        <Alert
          type="error"
          showIcon
          title={info.error instanceof ApiError ? info.error.message : String(info.error)}
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="版本和模型" loading={info.isPending} style={{ height: '100%' }}>
            {d && (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="JacarandaParse">v{d.version}</Descriptions.Item>
                <Descriptions.Item label="模式">
                  {d.mode === 'team' ? '团队模式' : '个人模式'}
                </Descriptions.Item>
                <Descriptions.Item label="模型服务">{d.model.server}</Descriptions.Item>
                <Descriptions.Item label="正在用的模型">
                  {d.model.loaded ?? <Typography.Text type="secondary">连不上模型服务</Typography.Text>}
                </Descriptions.Item>
                <Descriptions.Item label="上下文长度">
                  {d.model.context ? `${d.model.context.toLocaleString()} token` : '–'}
                </Descriptions.Item>
                <Descriptions.Item label="模型文件">
                  {d.model.file ?? (
                    <Typography.Text type="secondary">models/ 里没有找到 .gguf</Typography.Text>
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="OCR">
                  {d.ocr.engine}
                  {d.ocr.version ? ` ${d.ocr.version}` : ''}（PP-OCR 模型）
                </Descriptions.Item>
                <Descriptions.Item label="模板">
                  <Flex gap={4} wrap>
                    {d.templates.map((t) => (
                      <Tag key={t.name}>
                        {t.title}（{t.name} 第 {t.version} 版）
                      </Tag>
                    ))}
                  </Flex>
                </Descriptions.Item>
              </Descriptions>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="数据量" loading={info.isPending} style={{ height: '100%' }}>
            {d && (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="文档">
                  {d.data.documents} 份，{d.data.pages} 页
                </Descriptions.Item>
                <Descriptions.Item label="原始文件">{formatBytes(d.data.document_bytes)}</Descriptions.Item>
                <Descriptions.Item label="抽取的字段">{d.data.fields} 个</Descriptions.Item>
                <Descriptions.Item label="人工审核记录">{d.data.revisions} 条</Descriptions.Item>
                <Descriptions.Item label="导出">
                  {d.data.exports} 次，存了 {formatBytes(d.data.export_bytes)}
                </Descriptions.Item>
              </Descriptions>
            )}
          </Card>
        </Col>
      </Row>
      <Card
        title="服务状态"
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={info.isFetching}
            onClick={() => void info.refetch()}
          >
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
    </Flex>
  )
}
