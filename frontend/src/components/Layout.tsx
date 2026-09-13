import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { NavMenu } from './NavMenu';
import { resetCategoryCache } from '../hooks/useCategories';

// Settings lives in the "more" menu (see NavMenu) alongside the legal pages.
const NAV = [
  { to: '/', label: 'Closet', end: true },
  { to: '/connect', label: 'Connect' },
  { to: '/tutorials', label: 'Tutorials' },
];

export function Layout() {
  const { session, signOut } = useAuth();

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__inner">
          <NavLink to="/" className="brand" end>
            <span className="brand__mark" aria-hidden />
            weatherpruf
          </NavLink>

          <nav className="app-nav" aria-label="Main">
            {NAV.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end ?? false}
                className={({ isActive }) => `app-nav__link${isActive ? ' is-active' : ''}`}
              >
                {link.label}
              </NavLink>
            ))}
          </nav>

          <div className="app-header__account">
            <span className="app-header__email" title={session?.user.email ?? ''}>
              {session?.user.email}
            </span>
            <button
              type="button"
              className="button button--quiet"
              onClick={() => {
                resetCategoryCache();
                void signOut();
              }}
            >
              Sign out
            </button>
            <NavMenu />
          </div>
        </div>
      </header>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
