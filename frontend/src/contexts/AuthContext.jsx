import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import { AUTH_QUERY_KEY, AuthContext } from './auth-context';

async function fetchMe({ signal }) {
  const { data } = await apiClient.get('/auth/me/', { signal });
  return data;
}

export function AuthProvider({ children }) {
  const queryClient = useQueryClient();

  const meQuery = useQuery({
    queryKey: AUTH_QUERY_KEY,
    queryFn: fetchMe,
    // A 403 here just means "not signed in" - there is nothing transient
    // to retry, and retrying would only delay ProtectedRoute's decision.
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  // Every mutation below cancels the query before writing to its cache.
  // Without this, a slow *earlier* fetch of the same query (e.g. the
  // very first `/auth/me/` check, fired on mount and still in flight when
  // the user submits the login form seconds later) can resolve *after*
  // login's setQueryData and overwrite the just-set fresh user with that
  // stale, now-outdated result - login "succeeds", the dashboard flashes
  // in, and then the page silently reverts to Login. cancelQueries() makes
  // React Query discard that late result instead of applying it.
  async function login(email, password) {
    const { data } = await apiClient.post('/auth/login/', { email, password });
    await queryClient.cancelQueries({ queryKey: AUTH_QUERY_KEY });
    queryClient.setQueryData(AUTH_QUERY_KEY, data);
    return data;
  }

  async function logout() {
    await apiClient.post('/auth/logout/');
    await queryClient.cancelQueries({ queryKey: AUTH_QUERY_KEY });
    // `null`, not `undefined`/removeQueries: this keeps the query "settled"
    // (isLoading stays false) instead of dropping back into a loading
    // state that would make ProtectedRoute wait again before it can act.
    queryClient.setQueryData(AUTH_QUERY_KEY, null);
  }

  async function changePassword(currentPassword, newPassword) {
    const { data } = await apiClient.post('/auth/change-password/', {
      current_password: currentPassword,
      new_password: newPassword,
    });
    await queryClient.cancelQueries({ queryKey: AUTH_QUERY_KEY });
    queryClient.setQueryData(AUTH_QUERY_KEY, data);
    return data;
  }

  const user = meQuery.data ?? null;

  const value = {
    user,
    isStaff: Boolean(user?.is_staff),
    // True only while the very first check is in flight and there is no
    // settled answer yet (success OR error both count as settled). This is
    // the flag ProtectedRoute must wait on: deciding while this is still
    // true is what logs an already-authenticated user out on every page
    // refresh, since a fresh page load always starts with an empty cache.
    isLoading: meQuery.isLoading,
    login,
    logout,
    changePassword,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
