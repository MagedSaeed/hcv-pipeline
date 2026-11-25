import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';

const RESEARCH_LINKS = [
  ['/dashboard', 'Dashboard'],
  ['/resources', 'Resources'],
  ['/taxonomy', 'Taxonomy'],
  ['/prompts', 'Prompts'],
  ['/labels', 'Labels'],
];

function researchLinkClassName({ isActive }) {
  return `text-sm font-medium ${isActive ? 'text-brand-700' : 'text-gray-600 hover:text-gray-900'}`;
}

function ModeLink({ to, active, children }) {
  return (
    <Link
      to={to}
      aria-current={active ? 'page' : undefined}
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${
        active ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
      }`}
    >
      {children}
    </Link>
  );
}

// Two modes: Analyses for system analysts, Experiment Lab for the experiment screens and their menu.
export default function Layout() {
  const { user, isStaff, logout } = useAuth();
  const { pathname } = useLocation();
  const research = !pathname.startsWith('/analyses');

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200 bg-white print:hidden">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <span className="text-sm font-semibold text-gray-900">HCV Pipeline</span>
            <nav aria-label="Main navigation" className="inline-flex gap-1 rounded-lg bg-gray-100 p-1">
              <ModeLink to="/analyses" active={!research}>
                Analyses
              </ModeLink>
              <ModeLink to="/dashboard" active={research}>
                Experiment Lab
              </ModeLink>
            </nav>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <span className="text-sm text-gray-500">{user?.email}</span>
            <button
              type="button"
              onClick={() => logout()}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              Log out
            </button>
          </div>
        </div>
        {research && (
          <div className="border-t border-gray-100">
            <nav
              aria-label="Experiment Lab navigation"
              className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2"
            >
              {RESEARCH_LINKS.map(([to, label]) => (
                <NavLink key={to} to={to} className={researchLinkClassName}>
                  {label}
                </NavLink>
              ))}
              {/* Settings holds the OpenRouter key, so only staff see the link. */}
              {isStaff && (
                <NavLink to="/settings" className={researchLinkClassName}>
                  Settings
                </NavLink>
              )}
            </nav>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
