import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';

function navLinkClassName({ isActive }) {
  return `text-sm font-medium ${isActive ? 'text-brand-700' : 'text-gray-600 hover:text-gray-900'}`;
}

// The app shell: top nav plus the current user and a logout button, with
// the routed screen rendered into <Outlet/>. Wrapping ProtectedRoute around
// this ONE route (see App.jsx) rather than around every page individually
// means every screen nested under it - Dashboard, Taxonomy, Settings, and
// whatever later tasks add - gets the nav for free.
export default function Layout() {
  const { user, isStaff, logout } = useAuth();

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <nav className="flex items-center gap-5" aria-label="Main navigation">
            <span className="text-sm font-semibold text-gray-900">HCV Pipeline</span>
            <NavLink to="/dashboard" className={navLinkClassName}>
              Dashboard
            </NavLink>
            <NavLink to="/resources" className={navLinkClassName}>
              Resources
            </NavLink>
            <NavLink to="/taxonomy" className={navLinkClassName}>
              Taxonomy
            </NavLink>
            <NavLink to="/prompts" className={navLinkClassName}>
              Prompts
            </NavLink>
            <NavLink to="/labels" className={navLinkClassName}>
              Labels
            </NavLink>
            {/* Settings holds the OpenRouter key; a non-staff user should not
                even see a link to a form they cannot use. */}
            {isStaff && (
              <NavLink to="/settings" className={navLinkClassName}>
                Settings
              </NavLink>
            )}
          </nav>
          <div className="flex items-center gap-3">
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
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
