import { useQuery } from '@tanstack/react-query'
import { getPageImage } from '../../api/documents'

/**
 * 取某一页的图片，返回一个能放进 <img src> 的地址。
 *
 * <img src="/api/..."> 没法带登录 token，所以先用 fetch（带 token）把图片取回来，
 * 再用 URL.createObjectURL 变成一个浏览器内部的地址（blob:...）。
 * 同一页的图片不会变：取过一次就一直用缓存（staleTime: Infinity）。
 */
export function usePageImage(documentId: string, page: number) {
  return useQuery({
    queryKey: ['page-image', documentId, page],
    queryFn: async () => URL.createObjectURL(await getPageImage(documentId, page)),
    staleTime: Infinity,
  })
}
