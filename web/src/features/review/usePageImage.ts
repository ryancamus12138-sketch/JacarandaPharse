import { useQuery } from '@tanstack/react-query'
import { getPageImage } from '../../api/documents'

/**
 * Fetch a page image and return a URL usable in <img src>.
 *
 * <img src="/api/..."> cannot carry the login token, so the image is fetched with the token and turned into
 * an object URL (blob:...) with URL.createObjectURL. A page image never changes, so it is cached for good
 * (staleTime: Infinity).
 */
export function usePageImage(documentId: string, page: number) {
  return useQuery({
    queryKey: ['page-image', documentId, page],
    queryFn: async () => URL.createObjectURL(await getPageImage(documentId, page)),
    staleTime: Infinity,
  })
}
