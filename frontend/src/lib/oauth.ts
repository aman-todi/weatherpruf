/**
 * Supabase's OAuth 2.1 server does not render a consent screen of its own. It
 * redirects the user to this path with an `authorization_id` and expects the
 * app to collect the decision — so this constant has to match **Authentication
 * → OAuth Server → Authorization Path** in the dashboard exactly. Change one
 * and you must change the other, or Claude.ai's connector flow dead-ends.
 */
export const CONSENT_PATH = '/oauth/consent';

export function isConsentPath(): boolean {
  return window.location.pathname === CONSENT_PATH;
}

/**
 * Where a magic link should land the user.
 *
 * Normally the app root. On the consent screen it must be the *current* URL,
 * query string and all: the `authorization_id` identifies the pending OAuth
 * request, and a link back to the bare origin drops it. The user would arrive
 * signed in, at the closet, with Claude.ai still waiting on a redirect that
 * never comes.
 */
export function magicLinkRedirect(): string {
  return isConsentPath() ? window.location.href : window.location.origin;
}
