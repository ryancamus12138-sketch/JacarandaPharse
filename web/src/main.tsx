import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* ConfigProvider：整个网页用中文（日期、分页、"确定/取消"按钮）；主题色用蓝花楹的紫色 */}
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#7b5cc4' } }}>
      {/* AntApp：让弹出的提示框跟着上面的主题 */}
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
