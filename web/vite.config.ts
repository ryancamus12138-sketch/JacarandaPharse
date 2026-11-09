import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发时的设置。https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', // 只监听本机（项目规则 11）
    port: 5173,
    strictPort: true, // 5173 被占用就报错，不偷偷换端口
    proxy: {
      // 网页里请求 /api/...，由 Vite 转给本机的后端。浏览器只看到一个地址，不用处理跨域
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    chunkSizeWarningLimit: 2000, // Ant Design 本身就大；本机使用，不在乎几百 KB
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
})
