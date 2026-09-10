import { useAuth } from '../contexts/auth-context';

// Placeholder landing page. Task 1 only needs somewhere authenticated for
// ProtectedRoute to guard and for Login to redirect to; Task 2 replaces
// this with the real experiment/run listing (spec §10).
export default function Dashboard() {
  const { user, logout } = useAuth();

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
        <button
          type="button"
          onClick={() => logout()}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
        >
          Log out
        </button>
      </div>
      <p className="mt-2 text-sm text-gray-500">Signed in as {user?.email}</p>
    </main>
  );
}
