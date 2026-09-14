import { useAuth } from '../auth/AuthContext';

/**
 * Shown to a signed-in user who is beyond the sign-up cap (`max_users`). The
 * account exists in Supabase, but the app admits only the first N users by
 * sign-up order, so this one gets a friendly wall instead of the closet.
 */
export function CapacityNotice() {
  const { signOut } = useAuth();

  return (
    <div className="auth-page">
      <div className="auth-card">
        <span className="brand__mark brand__mark--large" aria-hidden />
        <h1>Weatherpruf is full right now</h1>
        <p className="auth-card__tagline">
          Sorry — we’re not taking new sign-ups at the moment. Please check back in a few weeks.
        </p>
        <button
          type="button"
          className="button button--quiet button--block"
          onClick={() => void signOut()}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
