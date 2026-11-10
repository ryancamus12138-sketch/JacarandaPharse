import type { DocumentStatus } from '../../api/types'

/** 还在路上的状态：列表要定时刷新，等它们变成完成或失败。 */
export const IN_PROGRESS: DocumentStatus[] = ['uploaded', 'processing']

/** 有结果、能导出的状态（和后端 EXPORTABLE 一样）。 */
export const EXPORTABLE: DocumentStatus[] = ['done', 'reviewed']
