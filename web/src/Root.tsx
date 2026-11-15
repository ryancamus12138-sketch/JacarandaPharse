import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from './api/client'
import App from './App.tsx'
import './i18n'

/** Server state management: caching, loading, errors, polling. */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retrying 4xx (forbidden, not found) is pointless, so show the error immediately; network errors and
      // 5xx are retried at most twice.
      retry: (count, err) => {
        const clientError = err instanceof ApiError && err.status >= 400 && err.status < 500
        return !clientError && count < 2
      },
    },
  },
})

/**
 * Root: Ant Design locale follows the interface language, and server data is refetched after a language
 * change because the backend localizes its messages per request (Accept-Language).
 */
export function Root() {
  const { i18n } = useTranslation()
  const english = i18n.language === 'en'
  useEffect(() => {
    const refetch = () => void queryClient.invalidateQueries()
    i18n.on('languageChanged', refetch)
    return () => i18n.off('languageChanged', refetch)
  }, [i18n])
  return (
    // ConfigProvider: Ant Design built-in texts (dates, pagination, OK/Cancel); jacaranda purple as the
    // primary color.
    <ConfigProvider
      locale={english ? enUS : zhCN}
      theme={{ token: { colorPrimary: '#7b5cc4', colorLink: '#7b5cc4' } }}
    >
      {/* AntApp: makes message and notification popups follow the theme above. */}
      <AntApp>
        <QueryClientProvider client={queryClient}>
          {/* Keyed by language: the whole interface is remounted on a switch, so texts computed once
              (memoized options, column definitions, the signed-in user) are rebuilt in the new
              language. The query cache lives outside and survives. */}
          <App key={i18n.language} />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  )
}
