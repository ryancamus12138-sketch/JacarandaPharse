import { Button, Result } from 'antd'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router'
import { AuthProvider } from './auth/AuthProvider'
import { LoginPage } from './auth/LoginPage'
import { ResetPasswordPage } from './auth/ResetPasswordPage'
import { RequireAuth, RequirePermission } from './auth/RequireAuth'
import { AppLayout } from './components/AppLayout'
import { AuditLogPage } from './features/admin/AuditLogPage'
import { UsersPage } from './features/admin/UsersPage'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { DocumentListPage } from './features/documents/DocumentListPage'
import { ExportsPage } from './features/exports/ExportsPage'
import { ReviewPage } from './features/review/ReviewPage'
import { SystemPage } from './features/system/SystemPage'

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
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="documents" element={<DocumentListPage />} />
            <Route path="documents/:id" element={<ReviewPage />} />
            <Route path="exports" element={<ExportsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route
              path="admin/users"
              element={
                <RequirePermission permission="user:manage">
                  <UsersPage />
                </RequirePermission>
              }
            />
            <Route
              path="admin/audit-logs"
              element={
                <RequirePermission permission="audit:read">
                  <AuditLogPage />
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
