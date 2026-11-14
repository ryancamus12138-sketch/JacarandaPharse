import { Button, Flex, Result, Spin } from 'antd'
import { lazy, type ReactNode, Suspense } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthProvider'
import { LoginPage } from './auth/LoginPage'
import { ResetPasswordPage } from './auth/ResetPasswordPage'
import { RequireAuth, RequirePermission } from './auth/RequireAuth'
import { AppLayout } from './components/AppLayout'

// 每个页面单独打包，打开时才下载：第一次打开网页要下载的东西少很多
const AuditLogPage = lazy(() =>
  import('./features/admin/AuditLogPage').then((m) => ({ default: m.AuditLogPage })),
)
const UsersPage = lazy(() =>
  import('./features/admin/UsersPage').then((m) => ({ default: m.UsersPage })),
)
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
const SystemPage = lazy(() =>
  import('./features/system/SystemPage').then((m) => ({ default: m.SystemPage })),
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

const ProfilePage = lazy(() =>
  import('./features/profile/ProfilePage').then((m) => ({ default: m.ProfilePage })),
)

/** 页面还在下载时显示转圈。 */
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

/** 路由：哪个网址显示哪个页面。 */
export default function App() {
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
            <Route path="system" element={<Page>
                  <SystemPage />
                </Page>} />
            <Route path="profile" element={<Page>
                  <ProfilePage />
                </Page>} />
            <Route
              path="admin/users"
              element={
                <RequirePermission permission="user:manage">
                  <Page>
                  <UsersPage />
                </Page>
                </RequirePermission>
              }
            />
            <Route
              path="admin/audit-logs"
              element={
                <RequirePermission permission="audit:read">
                  <Page>
                  <AuditLogPage />
                </Page>
                </RequirePermission>
              }
            />
            <Route
              path="*"
              element={
                <Result
                  status="404"
                  title="找不到这个页面"
                  extra={
                    <Link to="/">
                      <Button type="primary">回首页</Button>
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
