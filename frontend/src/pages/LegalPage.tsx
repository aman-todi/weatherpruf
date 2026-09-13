import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/**
 * Standalone wrapper for the Privacy and Terms pages. Kept outside the app
 * `Layout` so the pages render identically whether or not the visitor is signed
 * in — Google's OAuth verification (and any curious user) must be able to read
 * them without an account.
 */
export function LegalPage({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: ReactNode;
}) {
  return (
    <div className="legal-shell">
      <header className="legal-header">
        <div className="legal-header__inner">
          <Link to="/" className="brand">
            <span className="brand__mark" aria-hidden />
            weatherpruf
          </Link>
          <nav className="legal-header__links" aria-label="Legal">
            <Link to="/privacy">Privacy</Link>
            <Link to="/terms">Terms</Link>
          </nav>
        </div>
      </header>
      <main className="legal-main">
        <article className="legal-doc">
          <h1>{title}</h1>
          <p className="legal-doc__updated">Last updated: {updated}</p>
          {children}
        </article>
      </main>
    </div>
  );
}
