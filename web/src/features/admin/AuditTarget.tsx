import { Link } from 'react-router'
import type { AuditLog } from '../../api/types'

/** 这条日志的"对象"：文档能点进去；字段跳到它所在的文档。 */
export function AuditTarget({ log, userName }: { log: AuditLog; userName: (id: string) => string }) {
  const documentId =
    log.target_type === 'document' ? log.target_id : (log.detail.document_id as string | undefined)
  if (documentId) {
    return <Link to={`/documents/${documentId}`}>打开文档</Link>
  }
  if (log.target_type === 'user' && log.target_id) {
    return <>{userName(log.target_id)}</>
  }
  return null
}
