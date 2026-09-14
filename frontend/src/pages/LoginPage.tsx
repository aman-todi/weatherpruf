import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Banner } from '../components/Banner';
import { isConsentPath } from '../lib/oauth';

export function LoginPage() {
  const { signIn, signInWithGoogle } = useAuth();
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [googlePending, setGooglePending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // An assistant sent the user here to approve a connection and they turned out
  // not to be signed in. Saying so beats a bare login page mid-flow; the sign-in
  // itself returns them to the pending request (see `postLoginRedirect`).
  const forConsent = isConsentPath();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSending(true);
    try {
      await signIn(email);
      setSentTo(email.trim());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not send the link.');
    } finally {
      setSending(false);
    }
  };

  const continueWithGoogle = async () => {
    setError(null);
    setGooglePending(true);
    try {
      // Resolves by navigating away to Google, so we leave the button in its
      // pending state; only an error (nothing happened) resets it.
      await signInWithGoogle();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start Google sign-in.');
      setGooglePending(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <span className="brand__mark brand__mark--large" aria-hidden />
        <h1>Weatherpruf</h1>
        <p className="auth-card__tagline">
          {forConsent
            ? 'Sign in to finish connecting your assistant. The link brings you back to the approval screen.'
            : 'Your closet, catalogued once — then dressed every morning by an assistant that checks the weather for you.'}
        </p>

        {sentTo ? (
          <div className="auth-card__sent">
            <Banner tone="success">Check your inbox.</Banner>
            <p>
              We sent a sign-in link to <strong>{sentTo}</strong>. Open it on this device and you
              will land back here, signed in. There is no password to remember.
            </p>
            <button
              type="button"
              className="button button--quiet"
              onClick={() => {
                setSentTo(null);
                setError(null);
              }}
            >
              Use a different email
            </button>
          </div>
        ) : (
          <div className="auth-card__form">
            {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}
            <button
              type="button"
              className="button button--block auth-google"
              onClick={() => void continueWithGoogle()}
              disabled={googlePending || sending}
            >
              <GoogleMark />
              {googlePending ? 'Redirecting…' : 'Continue with Google'}
            </button>
            <div className="auth-divider">or</div>
            <form className="auth-card__form" onSubmit={submit}>
              <label className="field">
                <span className="field__label">Email</span>
                <input
                  type="email"
                  value={email}
                  required
                  autoComplete="email"
                  placeholder="you@example.com"
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>
              <button
                type="submit"
                className="button button--primary button--block"
                disabled={sending || !email.trim()}
              >
                {sending ? 'Sending…' : 'Email me a sign-in link'}
              </button>
              <p className="auth-card__fineprint">
                New here? Either option signs you up — nothing else to fill in.
              </p>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}

/** Google's four-colour "G" mark, for the sign-in button. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 18 18" aria-hidden focusable="false">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.71H.96v2.33A9 9 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.71a5.41 5.41 0 0 1 0-3.42V4.96H.96a9 9 0 0 0 0 8.08l3.01-2.33Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.47.89 11.43 0 9 0A9 9 0 0 0 .96 4.96l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z"
      />
    </svg>
  );
}
