import { useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthProvider';
import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { ClosetPage } from './pages/ClosetPage';
import { ConnectPage } from './pages/ConnectPage';
import { ConsentPage } from './pages/ConsentPage';
import { CapacityNotice } from './pages/CapacityNotice';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupNotice } from './pages/SetupNotice';
import { TutorialsPage } from './pages/TutorialsPage';
import { CONSENT_PATH } from './lib/oauth';
import { api, isApiError } from './lib/api';
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
      {/* Outside <Layout> and the admission gate: a consent screen reached
          mid-flow from Claude.ai should not offer the closet's navigation, and
          the OAuth handoff has its own gating. React Router ranks this above the
          catch-all below by specificity, not by order. */}
      <Route path={CONSENT_PATH} element={<ConsentPage />} />
      <Route
        element={
          <AdmissionGate>
            <Layout />
          </AdmissionGate>
        }
      >
        <Route index element={<ClosetPage />} />
        <Route path="connect" element={<ConnectPage />} />
        <Route path="tutorials" element={<TutorialsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

/**
 * Gate the signed-in app on the sign-up cap. `/api/me` (like every route) returns
 * a 403 `at_capacity` for a user beyond `max_users`; render the notice instead of
 * the closet. Any other failure falls through to the app, where each page shows
 * its own error — a capacity wall should never be an artefact of a flaky request.
 */
function AdmissionGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'checking' | 'ok' | 'at_capacity'>('checking');

  useEffect(() => {
    let active = true;
    api.me().then(
      () => active && setState('ok'),
      (cause: unknown) => {
        if (!active) return;
        setState(isApiError(cause) && cause.code === 'at_capacity' ? 'at_capacity' : 'ok');
      },
    );
    return () => {
      active = false;
    };
  }, []);

  if (state === 'checking') {
    return (
      <div className="auth-page">
        <p className="placeholder">Loading…</p>
      </div>
    );
  }
  if (state === 'at_capacity') return <CapacityNotice />;
  return <>{children}</>;
}
