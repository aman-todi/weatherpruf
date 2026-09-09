import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { Banner } from '../components/Banner';

export function LoginPage() {
  const { signIn } = useAuth();
  const [email, setEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <div className="auth-page">
      <div className="auth-card">
        <span className="brand__mark brand__mark--large" aria-hidden />
        <h1>weatherpruf</h1>
        <p className="auth-card__tagline">
          Your closet, catalogued once — then dressed every morning by an assistant that checks the
          weather for you.
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
          <form className="auth-card__form" onSubmit={submit}>
            {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}
            <label className="field">
              <span className="field__label">Email</span>
              <input
                type="email"
                value={email}
                required
                autoComplete="email"
                autoFocus
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
              New here? The same link signs you up. Nothing else to fill in.
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
