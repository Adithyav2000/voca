import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ThemeProvider } from "./context/ThemeContext";
import { UserProfileProvider } from "./context/UserProfileContext";
import { AuditTrailProvider } from "./context/AuditTrailContext";
import { Layout } from "./components/Layout";
import { Auth } from "./pages/Auth";
import { Dashboard } from "./pages/Dashboard";
import { SessionDetail } from "./pages/CampaignDetail";
import { Appointments } from "./pages/Appointments";
import { Admin } from "./pages/Admin";
import { Settings } from "./pages/Settings";

function Home() {
  const { authState, isLoggedIn } = useAuth();
  if (authState === "loading") return null;
  if (!isLoggedIn) return <Navigate to="/auth" replace />;
  return <Dashboard />;
}

function AuthRoute() {
  const { authState, isLoggedIn } = useAuth();
  if (authState === "loading") return null;
  if (isLoggedIn) return <Navigate to="/" replace />;
  return <Auth />;
}

function Protected({ children }: { children: ReactNode }) {
  const { authState, isLoggedIn } = useAuth();
  if (authState === "loading") return null;
  if (!isLoggedIn) return <Navigate to="/auth" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <UserProfileProvider>
          <AuditTrailProvider>
            <BrowserRouter>
              <Layout>
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/auth" element={<AuthRoute />} />
                  <Route path="/sessions/:sessionId" element={<Protected><SessionDetail /></Protected>} />
                  <Route path="/appointments" element={<Protected><Appointments /></Protected>} />
                  <Route path="/admin" element={<Protected><Admin /></Protected>} />
                  <Route path="/settings" element={<Protected><Settings /></Protected>} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Layout>
            </BrowserRouter>
          </AuditTrailProvider>
        </UserProfileProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}
