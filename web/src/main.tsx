import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'antd/dist/reset.css' // 去掉浏览器自带的页面边距等默认样式
import { ApiError } from './api/client'
import App from './App.tsx'

/** 管"从后端取数据"：缓存、加载中、出错、定时刷新。 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 4xx（没权限、找不到）重试也没用，直接显示错误；连不上或 5xx 最多再试 2 次
      retry: (count, err) => {
        const clientError = err instanceof ApiError && err.status >= 400 && err.status < 500
        return !clientError && count < 2
      },
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* ConfigProvider：整个网页用中文（日期、分页、"确定/取消"按钮）；主题色用蓝花楹的紫色 */}
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#7b5cc4', colorLink: '#7b5cc4' } }}>
      {/* AntApp：让弹出的提示框跟着上面的主题 */}
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
