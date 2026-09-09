import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { resetCategoryCache } from '../hooks/useCategories';

const NAV = [
  { to: '/', label: 'Closet', end: true },
  { to: '/connect', label: 'Connect' },
  { to: '/settings', label: 'Settings' },
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
          </div>
        </div>
      </header>

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
