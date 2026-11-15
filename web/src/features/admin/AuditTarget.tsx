import { Link } from 'react-router'
import type { AuditLog } from '../../api/types'
import { useTranslation } from 'react-i18next'

/** Target of an entry: documents are links; fields link to their document. */
export function AuditTarget({ log, userName }: { log: AuditLog; userName: (id: string) => string }) {
  const { t } = useTranslation()
  const documentId =
    log.target_type === 'document' ? log.target_id : (log.detail.document_id as string | undefined)
  if (documentId) {
    return <Link to={`/documents/${documentId}`}>{t('打开文档')}</Link>
  }
  if (log.target_type === 'user' && log.target_id) {
    return <>{userName(log.target_id)}</>
  }
  return null
}
