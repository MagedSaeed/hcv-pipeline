import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

// Response shapes here mirror backend/core/serializers.py's UserSerializer
// (id, email, name, is_staff, must_change_password) and the actual status
// codes/bodies backend/core/views.py produces - not invented shapes. In
// particular:
//   - an unauthenticated request gets 403 (not 401): DRF's
//     SessionAuthentication has no WWW-Authenticate challenge, so
//     rest_framework coerces NotAuthenticated's usual 401 down to 403. See
//     backend/core/tests/test_auth_api.py::test_me_requires_authentication.
//   - a bad login is a whole-serializer ValidationError, which DRF reports
//     as {"non_field_errors": [...]}, per LoginSerializer.validate() in
//     backend/core/serializers.py.
export const NOT_AUTHENTICATED_BODY = {
  detail: 'Authentication credentials were not provided.',
};

const SEED_USERS = {
  'researcher@example.com': {
    id: 1,
    email: 'researcher@example.com',
    name: 'Researcher',
    is_staff: false,
    must_change_password: false,
    password: 'correct-horse',
  },
  'newhire@example.com': {
    id: 2,
    email: 'newhire@example.com',
    name: '',
    is_staff: false,
    must_change_password: true,
    password: 'temp-password',
  },
};

function cloneSeedUsers() {
  return Object.fromEntries(Object.entries(SEED_USERS).map(([email, user]) => [email, { ...user }]));
}

let users = cloneSeedUsers();

// A tiny in-memory stand-in for Django's session, mutable so a test can log
// in, act, and log out and see the next request reflect it - the same
// sequencing a real session cookie would produce.
export const authState = { currentUserEmail: null };

export function resetAuthState() {
  users = cloneSeedUsers();
  authState.currentUserEmail = null;
}

function toUserResponse(user) {
  const { password: _password, ...rest } = user;
  return rest;
}

function currentUser() {
  return authState.currentUserEmail ? users[authState.currentUserEmail] : null;
}

export const handlers = [
  http.get('/api/auth/csrf/', () => {
    // A real Set-Cookie response header isn't reliably picked up by
    // document.cookie through msw's Node interceptors under jsdom, so this
    // stands in for the browser applying it. The client only cares that
    // the cookie exists afterward, which is exactly what this guarantees.
    document.cookie = 'csrftoken=test-csrf-token; path=/';
    return HttpResponse.json({ detail: 'CSRF cookie set.' });
  }),

  http.post('/api/auth/login/', async ({ request }) => {
    const body = await request.json();
    const user = users[body.email];
    if (!user || user.password !== body.password) {
      return HttpResponse.json({ non_field_errors: ['Invalid email or password.'] }, { status: 400 });
    }
    authState.currentUserEmail = user.email;
    // Django's login() rotates the CSRF token (see the backend test cited
    // above); simulate that rotation too, so a test can catch a client
    // that wrongly cached the pre-login token string.
    document.cookie = 'csrftoken=rotated-csrf-token; path=/';
    return HttpResponse.json(toUserResponse(user));
  }),

  http.get('/api/auth/me/', () => {
    const user = currentUser();
    if (!user) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    return HttpResponse.json(toUserResponse(user));
  }),

  http.post('/api/auth/logout/', () => {
    if (!currentUser()) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    authState.currentUserEmail = null;
    return new HttpResponse(null, { status: 204 });
  }),

  http.post('/api/auth/change-password/', async ({ request }) => {
    const user = currentUser();
    if (!user) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    const body = await request.json();
    if (body.current_password !== user.password) {
      return HttpResponse.json({ current_password: ['Current password is incorrect.'] }, { status: 400 });
    }
    user.password = body.new_password;
    user.must_change_password = false;
    return HttpResponse.json(toUserResponse(user));
  }),

  // Staff-only; used by tests exercising the response interceptor against a
  // real, non-auth endpoint. Mirrors backend/core/views.AppSettingsView
  // (IsAdminUser) closely enough for that purpose: anonymous is 403 with
  // the same NotAuthenticated-coerced-to-403 body used everywhere else.
  http.get('/api/settings/', () => {
    const user = currentUser();
    if (!user || !user.is_staff) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    return HttpResponse.json({
      openrouter_key_masked: '••••1234',
      key_source: 'database',
      updated_at: '2026-09-01T00:00:00Z',
    });
  }),
];

export const server = setupServer(...handlers);
