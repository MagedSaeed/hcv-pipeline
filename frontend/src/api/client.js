import axios from 'axios';

const UNSAFE_METHODS = new Set(['post', 'put', 'patch', 'delete']);

// These endpoints manage their own error UX: Login shows the API's own
// message on a failed attempt, and AuthContext/ProtectedRoute react to the
// `/auth/me/` query settling (with or without a user). None of them should
// be short-circuited by the global "you got logged out" handling below -
// the `/auth/me/` check in particular is *expected* to 403 for a visitor
// who has never logged in, and that is not a session that just expired.
const SELF_MANAGED_AUTH_PATHS = ['/auth/csrf/', '/auth/login/', '/auth/logout/', '/auth/me/', '/auth/change-password/'];

function isSelfManagedAuthPath(url = '') {
  return SELF_MANAGED_AUTH_PATHS.some((path) => url.endsWith(path));
}

function readCookie(name) {
  if (typeof document === 'undefined') return null;
  for (const pair of document.cookie ? document.cookie.split('; ') : []) {
    const separatorIndex = pair.indexOf('=');
    const key = separatorIndex === -1 ? pair : pair.slice(0, separatorIndex);
    if (key === name) {
      const value = separatorIndex === -1 ? '' : pair.slice(separatorIndex + 1);
      return decodeURIComponent(value);
    }
  }
  return null;
}

export const apiClient = axios.create({
  baseURL: '/api',
  withCredentials: true,
});

// Ensures the `csrftoken` cookie exists, hitting the network for it at most
// once. Deliberately NOT a one-time cached token *string*: Django's
// login() rotates the CSRF token (see
// backend/core/tests/test_auth_api.py::test_an_unsafe_request_on_a_real_session_needs_a_csrf_token),
// and the rotated value arrives as a Set-Cookie on the login response
// itself. Re-reading the cookie fresh on every unsafe request picks up
// that rotation for free, with no extra request - caching the *string*
// instead would silently start sending a stale, rejected token right
// after the first login of a session.
let csrfFetchPromise = null;

function ensureCsrfCookie() {
  const existing = readCookie('csrftoken');
  if (existing) return Promise.resolve(existing);
  if (!csrfFetchPromise) {
    csrfFetchPromise = apiClient
      .get('/auth/csrf/')
      .then(() => readCookie('csrftoken'))
      .finally(() => {
        csrfFetchPromise = null;
      });
  }
  return csrfFetchPromise;
}

apiClient.interceptors.request.use(async (config) => {
  const method = (config.method || 'get').toLowerCase();
  if (UNSAFE_METHODS.has(method)) {
    const token = await ensureCsrfCookie();
    if (token) {
      config.headers = config.headers ?? {};
      config.headers['X-CSRFToken'] = token;
    }
  }
  return config;
});

// Set once, from main.jsx, after the QueryClient is created. client.js
// cannot import AuthContext/main.jsx (that would be circular), so the
// response interceptor below reaches the cache through this reference
// instead.
let queryClientRef = null;

export function setQueryClient(queryClient) {
  queryClientRef = queryClient;
}

// DRF's SessionAuthentication supplies no WWW-Authenticate challenge, so
// rest_framework.views.APIView.handle_exception coerces an unauthenticated
// request's status from 401 to 403 (see rest_framework/views.py,
// `handle_exception`) - verified directly against this backend via
// backend/core/tests/test_auth_api.py::test_me_requires_authentication,
// which asserts 403. A real 401 is also handled here defensively, in case
// that ever changes upstream.
const NOT_AUTHENTICATED_DETAIL = 'Authentication credentials were not provided.';

function isAuthFailure(error) {
  const status = error.response?.status;
  if (status === 401) return true;
  return status === 403 && error.response?.data?.detail === NOT_AUTHENTICATED_DETAIL;
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url ?? '';
    if (!isSelfManagedAuthPath(url) && isAuthFailure(error)) {
      queryClientRef?.clear();
      // A plain DOM event, not a react-router navigate() call: client.js is
      // plain JS with no Router context of its own. Whatever is mounted
      // inside the Router (see App.jsx's UnauthorizedListener) does the
      // actual navigation.
      window.dispatchEvent(new CustomEvent('auth:unauthorized'));
    }
    // Never retried - the failed request is simply handed back to its
    // caller, once, exactly as it came in.
    return Promise.reject(error);
  }
);

export default apiClient;
