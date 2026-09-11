import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Banner } from '../components/Banner';
import { supabase } from '../lib/supabase';

/**
 * The consent screen for Supabase's OAuth 2.1 server.
 *
 * Supabase is the authorization server, but it does not render consent itself.
 * It sends the user here with an `authorization_id`, and this page approves or
 * denies on their behalf. Claude.ai's connector registers via DCR and then
 * walks a real person through exactly this screen, so it is the one piece of UI
 * in the whole flow that a user sees between "add connector" and a working
 * assistant.
 *
 * Both decisions end by leaving the SPA for the client's redirect URI. That is
 * the expected ending, not a failure.
 */

/**
 * Derived from the installed SDK rather than imported: `@supabase/supabase-js`
 * re-exports the client but not the OAuth server's types, and reaching into
 * `@supabase/auth-js` would mean depending on a transitive package.
 */
type AuthorizationDetails = Extract<
  NonNullable<Awaited<ReturnType<typeof supabase.auth.oauth.getAuthorizationDetails>>['data']>,
  { authorization_id: string }
>;

/** The host of a redirect URI, which is the part worth showing a human. */
function hostOf(uri: string): string {
  try {
    return new URL(uri).host;
  } catch {
    return uri;
  }
}

export function ConsentPage() {
  const [params] = useSearchParams();
  const authorizationId = params.get('authorization_id');
  const { session, signOut } = useAuth();

  const [details, setDetails] = useState<AuthorizationDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set once we are on our way to the OAuth client, so the buttons cannot be
  // pressed twice while the browser is still tearing the page down.
  const [leaving, setLeaving] = useState(false);
  // StrictMode runs effects twice in development. Fetching details twice is
  // harmless, but navigating away twice is not.
  const navigated = useRef(false);

  const leaveFor = useCallback((url: string) => {
    if (navigated.current) return;
    navigated.current = true;
    setLeaving(true);
    window.location.assign(url);
  }, []);

  useEffect(() => {
    if (!authorizationId) return;
    let active = true;

    void supabase.auth.oauth.getAuthorizationDetails(authorizationId).then(({ data, error: failure }) => {
      if (!active) return;
      if (failure || !data) {
        setError(
          failure?.message ??
            'Supabase did not recognise that authorization request. It may have expired.',
        );
        return;
      }
      // Supabase answers with a redirect instead of details when this user has
      // already consented to these scopes — nothing to ask, just send them on.
      if ('authorization_id' in data) {
        setDetails(data);
      } else {
        leaveFor(data.redirect_url);
      }
    });

    return () => {
      active = false;
    };
  }, [authorizationId, leaveFor]);

  const decide = useCallback(
    async (choice: 'approve' | 'deny') => {
      if (!authorizationId || leaving) return;
      setError(null);
      setLeaving(true);

      // `skipBrowserRedirect` so a failure surfaces on this page. Left to its
      // default the SDK navigates away itself, and an error would land the user
      // on the client's callback with nothing explaining what went wrong.
      const { data, error: failure } =
        choice === 'approve'
          ? await supabase.auth.oauth.approveAuthorization(authorizationId, {
              skipBrowserRedirect: true,
            })
          : await supabase.auth.oauth.denyAuthorization(authorizationId, {
              skipBrowserRedirect: true,
            });

      if (failure || !data) {
        setLeaving(false);
        setError(failure?.message ?? 'That decision could not be recorded. Try again.');
        return;
      }

      leaveFor(data.redirect_url);
    },
    [authorizationId, leaving, leaveFor],
  );

  // Reached directly rather than through an assistant's sign-in flow.
  if (!authorizationId) {
    return (
      <Shell title="Nothing to approve">
        <p className="auth-card__tagline">
          This page is part of connecting an assistant to your closet. It only does something when
          the assistant sends you here.
        </p>
        <Link className="button button--primary button--block" to="/connect">
          Go to Connect your assistant
        </Link>
      </Shell>
    );
  }

  if (error && !details) {
    return (
      <Shell title="That request could not be loaded">
        <Banner>{error}</Banner>
        <p className="auth-card__fineprint">
          Authorization requests are short-lived. Start the connection again from Claude.ai and you
          will land back here with a fresh one.
        </p>
        <Link className="button button--quiet button--block" to="/connect">
          Back to Connect your assistant
        </Link>
      </Shell>
    );
  }

  if (!details) {
    return (
      <Shell title="Checking the request…">
        <p className="placeholder">One moment.</p>
      </Shell>
    );
  }

  const clientName = details.client.name?.trim() || 'An application';
  const scopes = details.scope.split(/\s+/).filter(Boolean);

  return (
    <Shell title={`${clientName} wants access to your closet`}>
      <p className="auth-card__tagline">
        Approving lets it read and change your closet on your behalf, the same way you can here.
      </p>

      {error && <Banner onDismiss={() => setError(null)}>{error}</Banner>}

      <section className="card">
        <h2>Requesting</h2>
        <p className="card__lede">
          <strong>{clientName}</strong>
          {details.client.uri ? <> — {hostOf(details.client.uri)}</> : null}
        </p>
        <p className="auth-card__fineprint">
          You will be sent back to <code>{hostOf(details.redirect_uri)}</code> either way. If that is
          not where you started, deny this.
        </p>
      </section>

      {scopes.length > 0 && (
        <section className="card">
          <h2>Access requested</h2>
          <ul className="examples">
            {scopes.map((scope) => (
              <li key={scope}>
                <code>{scope}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card">
        <h2>Signing in as</h2>
        <p className="card__lede">{details.user.email || session?.user.email}</p>
        <p className="auth-card__fineprint">
          The connector is tied to this account. Anything it adds lands in this closet.
        </p>
      </section>

      <div className="button-row">
        <button
          type="button"
          className="button button--primary"
          disabled={leaving}
          onClick={() => void decide('approve')}
        >
          {leaving ? 'Connecting…' : 'Approve'}
        </button>
        <button
          type="button"
          className="button button--quiet"
          disabled={leaving}
          onClick={() => void decide('deny')}
        >
          Deny
        </button>
      </div>

      <button
        type="button"
        className="button button--quiet button--block"
        disabled={leaving}
        onClick={() => void signOut()}
      >
        Use a different account
      </button>
    </Shell>
  );
}

/**
 * Deliberately outside the app's `Layout`: this screen is reached mid-flow from
 * somewhere else, and showing the closet's navigation would invite the user to
 * wander off with Claude.ai still waiting on a redirect.
 */
function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="auth-page">
      <div className="auth-card auth-card--wide">
        <span className="brand__mark brand__mark--large" aria-hidden />
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}
