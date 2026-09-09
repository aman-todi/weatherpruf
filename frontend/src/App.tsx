import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthProvider';
import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { ClosetPage } from './pages/ClosetPage';
import { ConnectPage } from './pages/ConnectPage';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupNotice } from './pages/SetupNotice';
import { supabaseConfigured } from './lib/supabase';

export default function App() {
  if (!supabaseConfigured) return <SetupNotice />;

  return (
    <AuthProvider>
      <BrowserRouter>
        <Routed />
      </BrowserRouter>
    </AuthProvider>
  );
}

function Routed() {
  const { session, loading } = useAuth();

  // Wait for the first session check so a signed-in user never sees the login
  // page flash on reload, and so the magic-link redirect resolves first.
  if (loading) {
    return (
      <div className="auth-page">
        <p className="placeholder">Loading…</p>
      </div>
    );
  }

  if (!session) return <LoginPage />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<ClosetPage />} />
        <Route path="connect" element={<ConnectPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
