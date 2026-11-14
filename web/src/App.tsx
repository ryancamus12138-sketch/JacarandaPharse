import { Button, Flex, Result, Spin } from 'antd'
import { lazy, type ReactNode, Suspense } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthProvider'
import { LoginPage } from './auth/LoginPage'
import { ResetPasswordPage } from './auth/ResetPasswordPage'
import { RequireAuth } from './auth/RequireAuth'
import { AppLayout } from './components/AppLayout'
import { useTranslation } from 'react-i18next'

// Each page is a separate chunk downloaded on first visit, which greatly reduces the initial download.
const DashboardPage = lazy(() =>
  import('./features/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const DocumentListPage = lazy(() =>
  import('./features/documents/DocumentListPage').then((m) => ({ default: m.DocumentListPage })),
)
const InsightsPage = lazy(() =>
  import('./features/insights/InsightsPage').then((m) => ({ default: m.InsightsPage })),
)
const OcrSamplesPage = lazy(() =>
  import('./features/ocr-samples/OcrSamplesPage').then((m) => ({ default: m.OcrSamplesPage })),
)
const ExportsPage = lazy(() =>
  import('./features/exports/ExportsPage').then((m) => ({ default: m.ExportsPage })),
)
const ReviewPage = lazy(() =>
  import('./features/review/ReviewPage').then((m) => ({ default: m.ReviewPage })),
)
const HubPage = lazy(() =>
  import('./features/hub/HubPage').then((m) => ({ default: m.HubPage })),
)
const DetEditorPage = lazy(() =>
  import('./features/ocr-samples/DetEditorPage').then((m) => ({ default: m.DetEditorPage })),
)
const SettingsPage = lazy(() =>
  import('./features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)
const TemplatesPage = lazy(() =>
  import('./features/templates/TemplatesPage').then((m) => ({ default: m.TemplatesPage })),
)
const BoxTemplatePage = lazy(() =>
  import('./features/templates/BoxTemplatePage').then((m) => ({ default: m.BoxTemplatePage })),
)
const ExcelImportPage = lazy(() =>
  import('./features/templates/ExcelImportPage').then((m) => ({ default: m.ExcelImportPage })),
)
const TemplateEditorPage = lazy(() =>
  import('./features/templates/TemplateEditorPage').then((m) => ({
    default: m.TemplateEditorPage,
  })),
)


/** Spinner shown while a page chunk is loading. */
function Page({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <Flex justify="center" style={{ padding: 96 }}>
          <Spin size="large" />
        </Flex>
      }
    >
      {children}
    </Suspense>
  )
}

/** Routes: which page each URL shows. */
export default function App() {
  const { t } = useTranslation()
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<Page>
                  <DashboardPage />
                </Page>} />
            <Route path="documents" element={<Page>
                  <DocumentListPage />
                </Page>} />
            <Route path="documents/:id" element={<Page>
                  <ReviewPage />
                </Page>} />
            <Route path="exports" element={<Page>
                  <ExportsPage />
                </Page>} />
            <Route path="insights" element={<Page>
                  <InsightsPage />
                </Page>} />
            <Route path="ocr-samples" element={<Page>
                  <OcrSamplesPage />
                </Page>} />
            <Route path="ocr-samples/det/:documentId/:page" element={<Page>
                  <DetEditorPage />
                </Page>} />
            <Route path="hub" element={<Page>
                  <HubPage />
                </Page>} />
            <Route
              path="templates"
              element={
                <Page>
                  <TemplatesPage />
                </Page>
              }
            />
            <Route
              path="templates/from-boxes/:documentId"
              element={
                <Page>
                  <BoxTemplatePage />
                </Page>
              }
            />
            <Route
              path="templates/excel/:uploadId"
              element={
                <Page>
                  <ExcelImportPage />
                </Page>
              }
            />
            <Route
              path="templates/:id"
              element={
                <Page>
                  <TemplateEditorPage />
                </Page>
              }
            />
            <Route path="settings/:section?" element={<Page>
                  <SettingsPage />
                </Page>} />
            {/* Legacy URLs, now all under settings. */}
            <Route path="system" element={<Navigate to="/settings/about" replace />} />
            <Route path="profile" element={<Navigate to="/settings/personal" replace />} />
            <Route path="admin/users" element={<Navigate to="/settings/team" replace />} />
            <Route path="admin/audit-logs" element={<Navigate to="/settings/team" replace />} />
            <Route
              path="*"
              element={
                <Result
                  status="404"
                  title={t('找不到这个页面')}
                  extra={
                    <Link to="/">
                      <Button type="primary">{t('回首页')}</Button>
                    </Link>
                  }
                />
              }
            />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
