import { missingEnvVars } from '../lib/supabase';

/** Shown instead of the app when the build has no Supabase configuration. */
export function SetupNotice() {
  return (
    <div className="auth-page">
      <div className="auth-card auth-card--wide">
        <h1>Almost there</h1>
        <p className="auth-card__tagline">
          This build has no Supabase configuration, so there is nothing to sign in to yet.
        </p>
        <p>Copy the example environment file and fill it in:</p>
        <pre className="code-block">
          <code>cp frontend/.env.example frontend/.env.local</code>
        </pre>
        <p>Missing:</p>
        <ul className="examples">
          {missingEnvVars.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>
        <p className="auth-card__fineprint">
          Vite only reads <code>VITE_*</code> variables at build time — restart the dev server after
          editing the file.
        </p>
      </div>
    </div>
  );
}
