import { InboxOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { App, Upload } from 'antd'
import { ApiError } from '../../api/client'
import { uploadDocument } from '../../api/documents'

const ACCEPT = '.jpg,.jpeg,.png,.webp,.tif,.tiff,.pdf'
const MAX_MB = 50 // 和后端 .env 的 MAX_UPLOAD_MB 默认值一样；真正的限制在后端

/** 拖拽或点击上传，一次可以选多个文件。每个文件单独上传，传完列表自动刷新。 */
export function UploadArea() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const upload = useMutation({
    mutationFn: uploadDocument,
    onSuccess: (document) => {
      void message.success(`已上传 ${document.filename}，正在排队处理`)
    },
    onError: (err, file) => {
      const reason = err instanceof ApiError ? err.message : String(err)
      void message.error(`${file.name} 上传失败：${reason}`)
    },
    onSettled: () => {
      // 告诉 TanStack Query：文档列表过时了，重新取一次
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })

  return (
    <Upload.Dragger
      accept={ACCEPT}
      multiple
      showUploadList={false}
      beforeUpload={(file) => {
        if (file.size > MAX_MB * 1024 * 1024) {
          void message.error(`${file.name} 超过 ${MAX_MB} MB`)
          return Upload.LIST_IGNORE
        }
        upload.mutate(file)
        return false // 不用 Ant Design 自己的上传，我们已经交给 upload.mutate 了
      }}
      style={{ marginBottom: 16 }}
    >
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p className="ant-upload-text">把收据、发票拖到这里，或者点击选择文件</p>
      <p className="ant-upload-hint">
        支持图片（JPG、PNG、WebP、TIFF）和 PDF，单个文件最大 {MAX_MB} MB
      </p>
    </Upload.Dragger>
  )
}
